{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

let
  # The live Plex runtime embeds CPython 2.7 through "Plex Framework.bundle".
  # Everything under Contents/ therefore has to stay Python 2.7 compatible.
  # pypy27 is the only Python 2.7 interpreter available in nixpkgs, and it
  # parses the same grammar as CPython 2.7, so it is used as a syntax gate.
  # compile() never writes a .pyc next to the source.
  py27-compat = pkgs.writeShellScript "check-py27-compat" ''
    status=0
    for file in "$@"; do
      [ -f "$file" ] || continue
      ${lib.getExe pkgs.pypy27} -S -c 'import sys; compile(open(sys.argv[1], "rb").read(), sys.argv[1], "exec")' "$file" || status=1
    done
    exit "$status"
  '';
in
{
  # https://devenv.sh/basics/
  dotenv.enable = true;

  # https://devenv.sh/languages/
  # pytest and lxml are supplied by nixpkgs, so no uv/venv bootstrapping and no
  # network access at shell entry.
  languages.python = {
    enable = true;
    lsp.enable = false;
    package = pkgs.python312.withPackages (ps: [
      ps.lxml
      ps.pytest
    ]);
  };

  # https://devenv.sh/packages/
  packages = [
    pkgs.git
    pkgs.nixfmt
    pkgs.pypy27
    pkgs.ruff
  ];

  # https://devenv.sh/tests/
  # devenv 2.x runs `devenv test` as the `devenv:enterTest` task, so the
  # option value is wired to that task explicitly.
  enterTest = ''
    pytest -q
  '';
  tasks."devenv:enterTest".exec = config.enterTest;

  # https://devenv.sh/git-hooks/
  # Contents/Code/__init__.py is dense Python 2 source: linting or formatting it
  # with py3 tooling would only produce noise, so ruff is scoped to the tests.
  git-hooks.hooks = {
    nixfmt = {
      enable = true;
      files = "\\.nix$";
    };
    ruff = {
      enable = true;
      files = "^(conftest\\.py|tests/.*\\.py)$";
    };
    ruff-format = {
      enable = true;
      files = "^(conftest\\.py|tests/.*\\.py)$";
    };
    py27-compat = {
      enable = true;
      name = "py27-compat";
      entry = "${py27-compat}";
      language = "system";
      files = "^Contents/.*\\.py$";
      pass_filenames = true;
    };
  };

  # See full reference at https://devenv.sh/reference/options/
}
