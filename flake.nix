{
  description = "Control plane for the ai-final-review governance experiments";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: [ ps.pytest ]))
            pkgs.openssl
            pkgs.jq
            pkgs.gh
            pkgs.git
            pkgs.curl
          ];
        };
      });
    };
}
