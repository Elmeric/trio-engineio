# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- `__version__` is no more a public attribute of the package


### Miscellaneous 

- Fixed regression on `isort` config when running on GitHub Actions
- The Changelog format is now based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
- Developper tasks runner use the [Invoke](https://www.pyinvoke.org/) library instead of
dedicated bash scripts
- Project dependencies are now managed through GitHub [Dependabot](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/about-dependabot-version-updates) 
- Bump [`httpcore`](https://www.encode.io/httpcore/) from 0.16.0 to 0.16.1
- The usage example "tets_trio_client.py" is now in a dedicated "Examples" folder

## [0.1.0] - 2022-11-13

- First release.

[unreleased]: https://github.com/Elmeric/trio-engineio/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Elmeric/trio-engineio/releases/tag/v0.1.0
