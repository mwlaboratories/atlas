{ lib, buildPythonPackage, fetchPypi, hatchling, hatch-vcs, hatch-fancy-pypi-readme }:

let
  hatch-kicad = buildPythonPackage rec {
    pname = "hatch-kicad";
    version = "0.4.0";
    format = "wheel";
    src = fetchPypi {
      pname = "hatch_kicad";
      inherit version;
      format = "wheel";
      dist = "py3";
      python = "py3";
      hash = "sha256-UaL6FLoZ3+xoZLw7w3Z921m7qARZ4p3uCqJH2Y0G7EQ=";
    };
    dependencies = [ hatchling ];
    doCheck = false;
  };
in
buildPythonPackage rec {
  pname = "kbplacer";
  version = "0.15";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-W9+emVTbbXG1rw4ZsIr5xYHjgojxl13JdcwJQw0rico=";
  };

  build-system = [
    hatchling
    hatch-vcs
    hatch-fancy-pypi-readme
    hatch-kicad
  ];

  env.SETUPTOOLS_SCM_PRETEND_VERSION = version;

  doCheck = false;

  meta = {
    description = "KiCad plugin for automatic keyboard key placement";
    homepage = "https://github.com/adamws/kicad-kbplacer";
    license = lib.licenses.gpl3Only;
  };
}
