"""Shared shell configuration assembly, independent of Web or Qt extras."""
def assemble_desktop_ctx(cfg_path=None):
    from pyharness import cli
    return cli.assemble_ctx(cli._load_settings(cfg_path))
