"""The public surface, and everything that can be checked without a shell."""
import dataclasses
import pathlib
import subprocess
import sys

import pytest

import taskbargap


def test_public_api_is_exactly_what_is_documented():
    assert sorted(taskbargap.__all__) == [
        "Gap", "GapWatcher", "NotWindowsError", "enable_dpi_awareness",
        "find_gap", "place",
    ]
    for name in taskbargap.__all__:
        assert hasattr(taskbargap, name), name


def test_importing_it_pulls_in_nothing_third_party():
    """The zero-dependency claim in the README and SECURITY.md, enforced.

    A ruff autofix once rewrote a return annotation to `typing.Self` and, because
    the floor is 3.10, quietly added `typing_extensions` to a package that
    advertises no dependencies. It passed locally, where that module happened to
    be installed, and only failed on a clean CI runner. This checks the actual
    import graph instead of trusting either.
    """
    code = (
        "import sys;"
        " before = set(sys.modules);"
        " import taskbargap;"
        " roots = {m.split('.')[0] for m in set(sys.modules) - before};"
        " print(','.join(sorted(r for r in roots"
        "                       if r not in sys.stdlib_module_names"
        "                       and not r.startswith('_')"
        "                       and r != 'taskbargap')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == "", f"importing taskbargap pulled in third-party modules: {out}"


def test_ships_a_py_typed_marker():
    # PEP 561: without it, consumers' type checkers ignore every annotation here.
    assert (pathlib.Path(taskbargap.__file__).parent / "py.typed").is_file()


def test_version_matches_the_installed_metadata():
    metadata = pytest.importorskip("importlib.metadata")
    try:
        installed = metadata.version("taskbargap")
    except metadata.PackageNotFoundError:
        pytest.skip("taskbargap is not installed in this environment")
    assert installed == taskbargap.__version__


def test_import_does_not_bind_user32():
    # The package must import on any OS, and must not annotate a user32 handle
    # until something is actually called - that keeps `import taskbargap` free
    # of side effects for the host process. Checked in a clean interpreter.
    code = ("import sys, taskbargap;"
            " sys.exit(0 if taskbargap._win32._user32 is None else 1)")
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


class TestGap:
    def make(self, **kw):
        base = {"left": 788, "right": 3404, "top": 2076, "bottom": 2160,
                "scale": 1.75, "monitor": 1234, "measured": True}
        base.update(kw)
        return taskbargap.Gap(**base)

    def test_width_and_height(self):
        gap = self.make()
        assert gap.width == 2616
        assert gap.height == 84

    def test_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            self.make().left = 0

    def test_equality_by_value(self):
        assert self.make() == self.make()
        assert self.make() != self.make(measured=False)


class TestGapWatcherArguments:
    def test_rejects_a_non_positive_interval(self):
        with pytest.raises(ValueError):
            taskbargap.GapWatcher(1, interval=0)
        with pytest.raises(ValueError):
            taskbargap.GapWatcher(1, interval=-1)

    def test_starts_unstarted(self):
        watcher = taskbargap.GapWatcher(1)
        assert watcher.running is False
        assert watcher.gap is None
        assert watcher.last_error is None

    def test_stop_before_start_is_a_no_op(self):
        taskbargap.GapWatcher(1).stop()
