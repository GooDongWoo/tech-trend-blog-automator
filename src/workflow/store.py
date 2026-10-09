"""Atomic records and nonblocking OS locks; the kernel releases crashed owners."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
from src.workflow.models import WorkflowRun


class RunBusy(ValueError):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    data = value.model_dump(mode='json') if hasattr(value, 'model_dump') else value
    try:
        with temporary.open('w', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RunStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        from config import settings
        if any(self.root.is_relative_to(Path(p).resolve()) for p in (settings.blog_repo_path, settings.obsidian_vault_path)):
            raise ValueError('workflow output must be outside blog/Vault')
    def directory(self, run_id):
        if not re.fullmatch('[a-f0-9]{16}', run_id):
            raise ValueError('invalid run ID')
        return self.root / run_id
    def get(self, run_id):
        run = WorkflowRun.model_validate_json((self.directory(run_id) / 'run.json').read_text(encoding='utf-8'))
        if run.id != run_id:
            raise ValueError('run identity changed')
        return run
    def save(self, run):
        atomic_json(self.directory(run.id) / 'run.json', run)
    @contextmanager
    def lock(self, run_id):
        directory = self.directory(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        # Never unlink lock inode: a waiter must lock the same file as its owner.
        with (directory / '.lock').open('a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError):
                raise RunBusy('run busy; retry after the active operation') from None
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
