{ lib
, buildPythonApplication
, buildPythonPackage
, callPackage
, fetchFromGitHub
, fetchPypi
, setuptools
, poetry-core
, pydantic
, pyparsing
, pcpp
, pyyaml
, platformdirs
, pydantic-settings
, tree-sitter
}:
let
  tree-sitter-devicetree = callPackage ./tree-sitter-devicetree.nix {};

  # Pin tree-sitter to 0.24.0 (keymap-drawer requires >=0.24.0,<0.25.0)
  tree-sitter-pinned = tree-sitter.overridePythonAttrs (old: rec {
    version = "0.24.0";
    src = fetchPypi {
      pname = "tree-sitter";
      inherit version;
      hash = "sha256-q9la9lyi9Pfso1Y0M5HtZp52Tzd0i1NSlG8A9/x45zQ=";
    };
    doCheck = false;  # Tests not present in this version
  });
in
buildPythonApplication rec {
  pname = "keymap-drawer";
  version = "0.22.1";
  pyproject = true;

  src = fetchFromGitHub {
    owner = "caksoylar";
    repo = pname;
    rev = "v${version}";
    hash = "sha256-X3O5yspEdey03YQ6JsYN/DE9NUiq148u1W6LQpUQ3ns=";
  };

  build-system = [ poetry-core ];

  postPatch = ''
    substituteInPlace pyproject.toml \
      --replace-warn "[tool.poetry]" "[tool.poetry]
name = \"${pname}\"
version = \"${version}\"
description = \"${meta.description}\"
authors = [\"Cem Aksoylar <caksoylar@gmail.com>\"]"
  '';

  propagatedBuildInputs = [
    pydantic
    pcpp
    pyyaml
    platformdirs
    pydantic-settings
    pyparsing
    tree-sitter-pinned
    tree-sitter-devicetree
  ];

  doCheck = false;

  meta = {
    homepage = "https://github.com/caksoylar/keymap-drawer";
    description = "Parse QMK & ZMK keymaps and draw them as vector graphics";
    license = lib.licenses.mit;
  };
}
