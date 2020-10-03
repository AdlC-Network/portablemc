# Portable Minecraft Launcher
An easy to use portable Minecraft launcher in only one Python script !
This single-script launcher is still compatible with the official (Mojang) Minecraft Launcher stored in `.minecraft` and use it.

![illustration](https://github.com/mindstorm38/portablemc/blob/master/illustration.png?raw=true)

***I used Python 3.8 to develop this launcher, further testing using prior version are welcome.***

Once you had the script, you can launch it using python (e.g `python portablemc.py`).

## Arguments
The launcher support various arguments that make it really usefull and faster than the official launcher
to test the game in offline mode *(custom username and UUID)*, or demo mode for example.

*You can read the complete help message using `-h` argument.*

#### Minecraft version
By default the launcher start the latest release version, to change this, you can use the `-v` *(`--version`)* followed by the
version name, or `snapshot` to target the latest snapshot, `release` do the same for latest release.

#### Username and UUID
By default, a random player [UUID](https://fr.wikipedia.org/wiki/Universally_unique_identifier) is used, and the username is
extract from the first part of the UUID's represention *(for a `110e8400-e29b-11d4-a716-446655440000` uuid, the username will be `110e8400`)*.

You can use `-u` *(`--username`)* argument followed by the username and `-i` *(`--uuid`)* with your user UUID.

*Online mode is not yet available.*

> Not that even if you have set another UUID, the username will be the same as default (with extracted part from default UUID).

#### Demo mode
Demo mode is a mostly unknown feature that enable to start the game with a restricted play duration, it is disabled by default.
Use `--demo` argument to enable it.

#### Window resolution
You can set the default window resolution *(do not affect the game if already in fullscreen mode)* by using `--resol` followed by
`<width>x<height>`, `width` and `height` are positive integers.

#### No start mode
By using `--nostart` flag, you force the launcher to download all requirements to the game, but do not start it.
