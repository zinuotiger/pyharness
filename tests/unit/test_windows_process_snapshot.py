"""Process-number reuse must never turn older host tools into owned children."""
import pytest
from tests.windows_process_snapshot import descendant_identities


def row(pid,parent,birth,name='node.exe'):
    return dict(ProcessId=pid,ParentProcessId=parent,CreationDate=str(birth),Name=name)


def test_reused_root_pid_excludes_old_host_tree_and_keeps_real_browser_tree():
    rows=[row(10,0,100,'python.exe'),row(20,10,50),row(21,20,110),
          row(30,10,101),row(31,30,102,'chrome.exe')]
    assert descendant_identities(rows,10)==[(30,'101'),(31,'102')]


def test_reused_intermediate_pid_is_checked_at_each_generation():
    rows=[row(10,0,100,'python.exe'),row(20,10,200),row(30,20,150),
          row(40,30,250),row(50,20,201,'chrome.exe')]
    assert descendant_identities(rows,10)==[(20,'200'),(50,'201')]


def test_non_browser_bridge_and_equal_timestamps_preserve_owned_descendants():
    rows=[row(10,0,100,'python.exe'),row(20,10,100,'cmd.exe'),
          row(30,20,101,'CHROME.EXE')]
    assert descendant_identities(rows,10)==[(30,'101')]


def test_missing_owned_root_is_unknown_rather_than_empty_success():
    with pytest.raises(RuntimeError,match='Owned root absent'):
        descendant_identities([row(20,10,50)],10)
