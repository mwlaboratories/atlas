{ lib, buildPythonPackage, fetchPypi, hatchling, sexpdata, pydantic, mcp, fastmcp, jinja2, typing-extensions }:

buildPythonPackage rec {
  pname = "kicad_sch_api";
  version = "0.5.6";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-8AH6/98XRdelLpBja+K8SuNGOQlJH/+fnUD+H3hC024=";
  };

  build-system = [ hatchling ];

  dependencies = [
    sexpdata
    pydantic
    mcp
    fastmcp
    jinja2
    typing-extensions
  ];

  doCheck = false;

  pythonImportsCheck = [ "kicad_sch_api" ];

  meta = with lib; {
    description = "Programmatic creation and manipulation of KiCad schematic files";
    homepage = "https://github.com/circuit-synth/kicad-sch-api";
    license = licenses.mit;
  };
}
