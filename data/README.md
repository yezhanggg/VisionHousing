# data/

This folder holds downloaded source files and built state data packs on your machine.
It is empty in the repository on purpose: no data is stored in git.

- `make data STATE=PA` fetches every source from its official publisher and builds the pack.
- Everything here except this file and `.gitkeep` is ignored by git.
- You can delete the contents at any time; the next `make data` downloads them again.
