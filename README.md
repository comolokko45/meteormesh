# MeteorMesh

MeteorMesh is an experimental system for collecting meteor-observation video from Android cameras and processing it on a local Ubuntu MeteorHub.

This repository hosts the MeteorHub 0.9 source ZIP for installation and testing. Android apps, hardware performance and scientific detection accuracy require real-world validation. Automatic deletion is disabled.

Data layout on the HP G62:
- `/opt/meteormesh`: software on SSD
- `/srv/meteor-data`: phone videos and analysis data on HDD


## Complete G62 software snapshot — 6 November 2026

See [snapshot installation and recovery](snapshots/g62-20261106/INSTALL.md). Contains full G62 software, encrypted configuration/identity recovery, Android sources/APKs and dependency records. Videos and database contents are excluded. Use the snapshot restore.py instead of the older root install.sh. The recovery key is stored separately and is not published.
