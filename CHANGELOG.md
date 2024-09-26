All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog], and this project adheres
to [Semantic Versioning].

## [Unreleased]

### Miscellaneous

- Bump black version to at lesat 24.3.0

## [0.2.0] - 2024-09-26

### Miscellaneous

- Minimum Python version is now 3.9
- Documentation dependencies are now in a dedicated Poetry "docs" group

## [0.1.1] - 2022-12-03

### Fixed
- Import error when trying to import trio-typing. It is now only imported for 
static type checking in develeopment environment.

### Changed
- `__version__` is no more a public attribute of the package

### Documentation
- Create the library documentation site on GitHub Pages

### Miscellaneous

- Fixed regression on `isort` config when running on GitHub Actions
- The Changelog format is now based on [Keep a Changelog]
- Developper tasks runner use the [Invoke] library instead of
dedicated bash scripts
- Project dependencies are now managed through GitHub [Dependabot] 
- Bump [`httpcore`][httpcore] from 0.16.0 to 0.16.1
- The usage example "tets_trio_client.py" is now in a dedicated "Examples" folder

## [0.1.0] - 2022-11-13

- First release.

[unreleased]: https://github.com/Elmeric/trio-engineio/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Elmeric/trio-engineio/compare/v0.2.0...v0.2.0
[0.2.0]: https://github.com/Elmeric/trio-engineio/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Elmeric/trio-engineio/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Elmeric/trio-engineio/releases/tag/v0.1.0

[Keep a Changelog]: https://keepachangelog.com/en/1.0.0/
[Semantic Versioning]: https://semver.org/spec/v2.0.0.html
[Invoke]: https://www.pyinvoke.org/
[Dependabot]: https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/about-dependabot-version-updates
[httpcore]: https://www.encode.io/httpcore/
