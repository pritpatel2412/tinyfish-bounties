## Description

Please include a summary of the change and which issue / service it addresses.

- Application affected:
  - [ ] Time Machine (`apps/timemachine/`)
  - [ ] TinyVerify (`apps/tinyverify/`)
  - [ ] Site to MCP (`apps/site2mcp/`)
  - [ ] tinycore foundation (`tinycore/`)

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Checklist

- [ ] My code follows the code style and guidelines of this project.
- [ ] I have executed `python scripts/scan_secrets.py` and confirmed zero secret leakage.
- [ ] I have run `pytest -m "not live"` locally and all tests pass (55/55).
- [ ] I have updated relevant documentation if my change impacts public interfaces.
