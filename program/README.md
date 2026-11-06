# MeteorMesh complete readable sources

This directory expands the6November2026 G62 program snapshot into normal GitHub files.

- G62 code/releases: [rootfs/home/meteor/.local/share/meteormesh/releases](rootfs/home/meteor/.local/share/meteormesh/releases)
- Existing helpers: [rootfs/home/meteor/.local/lib/meteormesh](rootfs/home/meteor/.local/lib/meteormesh)
- systemd: [rootfs/home/meteor/.config/systemd/user](rootfs/home/meteor/.config/systemd/user)
- MetDetPy: [rootfs/opt/meteormesh/MetDetPy](rootfs/opt/meteormesh/MetDetPy)
- Android sources/build scripts: [android](android)
- Dependency versions: [environment](environment)

Current components: HubRC191/RC211, captureRC221, monitor/managementRC222, AgentRC220, retentionRC217, MeteorNode0.13.6, Mobile1.1.0. Historical releases are retained because active units/imports reference multiple paths.

Secrets are not published in readable form. Exact secret configuration and source files containing a configuration secret remain in the authenticated encrypted recovery vault. The decrypted original is restored using the separately held key. No videos or runtime database rows are included. Restore tools, hashes and installation guide are at [snapshots/g62-20261106](../snapshots/g62-20261106/INSTALL.md). Source reads require no decryption key; exact credential recovery does.


## RC223 security corrections

Read [SECURITY_RC223.md](SECURITY_RC223.md). Corrected G62 hooks/policy and restore tools are active. MeteorNode0.13.7-security1 source and APK are supplied; phone installation remains pending.
