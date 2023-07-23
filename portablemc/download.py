"""Definition of the optimized download task.
"""

from http.client import HTTPConnection, HTTPSConnection, HTTPException
from threading import Thread
from pathlib import Path
from queue import Queue
import urllib.parse
import hashlib
import time
import os

from .task import Task, State, Watcher

from typing import Optional, Dict, List, Tuple, Union, Iterator


class DownloadTask(Task):
    """A download task.

    This task performs a mass download of files, this is basically
    used to download assets and libraries.

    :in(setup) DownloadList: This task adds and uses a `DownloadList` state.
    """

    def setup(self, state: State) -> None:
        # Do not overwrite potentially existing list, this allows multiple 
        # download tasks to be chained if needed.
        if DownloadList not in state:
            state.insert(DownloadList())

    def execute(self, state: State, watcher: Watcher) -> None:

        dl = state[DownloadList]
        entries_count = len(dl.entries)

        if not entries_count:
            return
        
        # Note: do not create more thread than available entries.
        threads_count = min(entries_count, (os.cpu_count() or 1) * 4)
        errors = []

        watcher.handle(DownloadStartEvent(threads_count, entries_count, dl.size))

        for result_count, result in dl.download(threads_count):
            if isinstance(result, DownloadResultProgress):
                watcher.handle(DownloadProgressEvent(
                    result.thread_id,
                    result_count,
                    result.entry,
                    result.size,
                    result.speed
                ))
            elif isinstance(result, DownloadResultError):
                errors.append((result.entry, result.code))

        # If errors are present, raise an error.
        if len(errors):
            raise DownloadError(errors)
        
        # Clear entries if successful, therefore multiple DownloadTask can be chained if
        # needed, without re-downloading the same files.
        dl.entries.clear()
        
        watcher.handle(DownloadCompleteEvent())


class DownloadEntry:
    """A download entry for the download task.
    """
    
    __slots__ = "url", "size", "sha1", "dst", "name", "executable"

    def __init__(self, 
        url: str, 
        dst: Path, *, 
        size: Optional[int] = None, 
        sha1: Optional[str] = None, 
        name: Optional[str] = None,
        executable: bool = False
    ) -> None:
        self.url = url
        self.dst = dst
        self.size = size
        self.sha1 = sha1
        self.name = url if name is None else name
        self.executable = executable

    def __repr__(self) -> str:
        return f"<DownloadEntry {self.name}>"

    def __hash__(self) -> int:
        # Making size and sha1 in the hash is useful to make them, 
        # this means that once added to a dictionary, these attributes
        # should not be modified.
        return hash((self.url, self.dst, self.size, self.sha1))

    def __eq__(self, other):
        return (self.url, self.dst, self.size, self.sha1) == (other.url, other.dst, other.size, other.sha1)


class _DownloadEntry:
    """Internal class with already parsed URL.
    """

    __slots__ = "https", "host", "entry"

    def __init__(self, https: bool, host: str, entry: DownloadEntry) -> None:
        self.https = https
        self.host = host
        self.entry = entry
    
    @classmethod
    def from_entry(cls, entry: DownloadEntry) -> "_DownloadEntry":

        # We only support HTTP/HTTPS
        url_parsed = urllib.parse.urlparse(entry.url)
        if url_parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported scheme '{url_parsed.scheme}://' from url {entry.url}")
        
        return cls(
            url_parsed.scheme == "https",
            url_parsed.netloc,
            entry)


class DownloadResult:
    """Base class for download result yielded by `DownloadList.download` function.
    """
    __slots__ = "thread_id", "entry"
    def __init__(self, thread_id: int, entry: DownloadEntry) -> None:
        self.thread_id = thread_id
        self.entry = entry


class DownloadResultProgress(DownloadResult):
    """Subclass of result when a file's download has been successful.
    """
    __slots__ = "size", "speed"
    def __init__(self, thread_id: int, entry: DownloadEntry, size: int, speed: float) -> None:
        super().__init__(thread_id, entry)
        self.size = size
        self.speed = speed


class DownloadResultError(DownloadResult):
    """Subclass of result when a file's download has failed.
    """
    __slots__ = "code",
    def __init__(self, thread_id: int, entry: DownloadEntry, code: str) -> None:
        super().__init__(thread_id, entry)
        self.code = code


class DownloadList:
    """ A download list.
    """

    __slots__ = "entries", "count", "size"

    def __init__(self):
        self.entries: List[_DownloadEntry] = []
        self.count = 0
        self.size = 0
    
    def clear(self) -> None:
        """Clear the download entry, removing all entries and computed count/size.
        """
        self.entries.clear()
        self.count = 0
        self.size = 0

    def add(self, entry: DownloadEntry, *, verify: bool = False) -> None:
        """Add a download entry to this list.

        :param entry: The entry to add.
        :param verify: Set to true in order to check if the file exists and has the same
        size has the given entry, in such case the entry is not added.
        """

        if verify and entry.dst.is_file() and (entry.size is None or entry.size == entry.dst.stat().st_size):
            return
        
        self.entries.append(_DownloadEntry.from_entry(entry))
        self.count += 1
        if entry.size is not None:
            self.size += entry.size
    
    def download(self, threads_count: int) -> Iterator[Tuple[int, DownloadResult]]:
        """Execute the download.
        
        :param threads_count: The number of threads to run the download on.
        :return: This function returns an iterator that yields a tuple that contain the
        total number of results and the new result that came in.
        """

        # Sort our entries in order to download big files first, this is allows better
        # parallelization at start and avoid too much blocking at the end of the download.
        # Note that entries without size are considered 1 Mio, to download early.
        self.entries.sort(key=lambda e: e.entry.size or 1048576, reverse=True)

        entries_count = len(self.entries)
        if not entries_count or threads_count < 1:
            return

        threads: List[Thread] = []

        entries_queue = Queue()
        result_queue = Queue()

        for th_id in range(threads_count):
            th = Thread(target=_download_thread, 
                        args=(th_id, entries_queue, result_queue), 
                        daemon=True, 
                        name=f"Download Thread {th_id}")
            th.start()
            threads.append(th)
        
        result_count = 0

        for entry in self.entries:
            entries_queue.put(entry)
        
        while result_count < entries_count:
            result = result_queue.get()
            result_count += 1
            yield result_count, result

        # Send 'threads_count' sentinels.
        for th_id in range(threads_count):
            entries_queue.put(None)

        # We intentionally don't join thread because it takes some time for unknown 
        # reason. And we don't care of these threads because these are daemon ones.


def _download_thread(
    thread_id: int, 
    entries_queue: Queue,
    result_queue: Queue,
):
    """This function is internally used for multi-threaded download.

    :param entries_queue: Where entries to download are received.
    :param result_queue: Where threads send progress update.
    """
    
    # Cache for connections depending on host and https
    conn_cache: Dict[Tuple[bool, str], Union[HTTPConnection, HTTPSConnection]] = {}

    # Each thread has its own buffer.
    buffer_back = bytearray(65536)
    buffer = memoryview(buffer_back)
    
    # Maximum tries count or a single entry.
    max_try_count = 3

    # For speed calculation.
    # total_time = 0
    # total_size = 0
    speed_smoothing = 0.005
    speed = 0

    while True:

        raw_entry: Optional[_DownloadEntry] = entries_queue.get()

        # None is a sentinel to stop the thread, it should be consumed ONCE.
        if raw_entry is None:
            break

        conn_key = (raw_entry.https, raw_entry.host)
        conn = conn_cache.get(conn_key)

        if conn is None:
            conn_type = HTTPSConnection if raw_entry.https else HTTPConnection
            conn = conn_cache[conn_key] = conn_type(raw_entry.host)

        entry = raw_entry.entry
        
        # Allow modifying this URL when redirections happen.
        # size_target = 0 if entry.size is None else entry.size
        
        last_error: Optional[str] = None
        try_num = 0

        while True:

            try_num += 1
            if try_num > max_try_count:
                # Retrying implies that we have set an error.
                assert last_error is not None
                result_queue.put(DownloadResultError(thread_id, entry, last_error))
                break
            
            start_time = time.monotonic()
            
            try:
                conn.request("GET", entry.url)
                res = conn.getresponse()
            except (ConnectionError, OSError, HTTPException):
                last_error = DownloadError.CONNECTION
                continue

            if res.status == 301 or res.status == 302:

                redirect_url = res.headers["location"]
                redirect_entry = DownloadEntry(
                    redirect_url, 
                    entry.dst, 
                    size=entry.size, 
                    sha1=entry.sha1, 
                    name=entry.name)
                
                entries_queue.put(_DownloadEntry.from_entry(redirect_entry))
                break  # Abort on redirect

            elif res.status != 200:

                # This loop is used to skip all bytes in the stream, 
                # and allow further request.
                while res.readinto(buffer):
                    pass

                last_error = DownloadError.NOT_FOUND
                continue

            sha1 = None if entry.sha1 is None else hashlib.sha1()
            size = 0

            entry.dst.parent.mkdir(parents=True, exist_ok=True)

            with entry.dst.open("wb") as dst_fp:

                while True:

                    read_len = res.readinto(buffer)
                    if not read_len:
                        break

                    buffer_view = buffer[:read_len]
                    size += read_len
                    if sha1 is not None:
                        sha1.update(buffer_view)
                    dst_fp.write(buffer_view)
            
            # If the entry should be executable, only those that can read would be
            # able to execute it.
            if entry.executable:
                prev_mode = entry.dst.stat().st_mode
                entry.dst.chmod(prev_mode | ((prev_mode & 0o444) >> 2))
            
            # total_time += time.monotonic() - start_time
            # total_size += size

            # Update speed calculation.
            elapsed_time = time.monotonic() - start_time
            if elapsed_time > 0:
                current_speed = size / (time.monotonic() - start_time)
                speed = speed_smoothing * current_speed + (1 - speed_smoothing) * speed

            if entry.size is not None and size != entry.size:
                last_error = DownloadError.INVALID_SIZE
            elif sha1 is not None and sha1.hexdigest() != entry.sha1:
                last_error = DownloadError.INVALID_SHA1
            else:
                
                result_queue.put(DownloadResultProgress(
                    thread_id,
                    entry,
                    size,
                    speed
                ))

                # Breaking means success.
                break

            # We are here only when the file download has started but checks have failed,
            # then we should remove the file.
            entry.dst.unlink(missing_ok=True)


class DownloadStartEvent:
    __slots__ = "threads_count", "entries_count", "size"
    def __init__(self, threads_count: int, entries_count: int, size: int) -> None:
        self.threads_count = threads_count
        self.entries_count = entries_count
        self.size = size

class DownloadProgressEvent:
    __slots__ = "thread_id", "count", "entry", "size", "speed"
    def __init__(self, thread_id: int, count: int, entry: DownloadEntry, size: int, speed: float) -> None:
        self.thread_id = thread_id
        self.count = count
        self.entry = entry
        self.size = size
        self.speed = speed

class DownloadCompleteEvent:
    __slots__ = tuple()


class DownloadError(Exception):
    """Raised when the downloader failed to download some entries.
    """

    CONNECTION = "connection"
    NOT_FOUND = "not_found"
    INVALID_SIZE = "invalid_size"
    INVALID_SHA1 = "invalid_sha1"

    def __init__(self, errors: List[Tuple[DownloadEntry, str]]) -> None:
        super().__init__()
        self.errors = errors