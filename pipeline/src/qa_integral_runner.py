"""Supervisor lock and the toolless `codex exec` command shared by the audit readers.

The 060 integral reader (its prompt, schema and two-reader launcher) was retired by spec 069
F5 — see git history. `supervisor_lock` is the ONE `runner.lock` of every audit family and
`command` is the blind Astra invocation `qa_dual_readers.run_astra` uses for the 063.
"""
from contextlib import contextmanager
import fcntl
from pathlib import Path

from .qa_integral import MODEL, EFFORT


@contextmanager
def supervisor_lock(root):
    # ONE lock for every audit family (data/audit/runner.lock): 060 (integral/<run>) and 061 (dual/<run>)
    # roots both resolve to the same grandparent, so two supervisors can never run four readers at once.
    path=Path(root).resolve().parent.parent/'runner.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('another integral supervisor is running')
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)


def command(work, schema, output, effort=EFFORT):
    cmd=['codex','exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
         '-C',str(work),'-s','read-only','-m',MODEL,'-c',f'model_reasoning_effort="{effort}"',
         '-c','project_doc_max_bytes=0','--disable','shell_tool','--disable','plugins',
         '--disable','apps','--disable','multi_agent','--disable','computer_use',
         '--disable','browser_use','--disable','in_app_browser','--disable','hooks',
         '--disable','view_image','--disable','skill_search','--enable','skip_host_skill_discovery',
         '-c','suppress_unstable_features_warning=true',
         '--output-schema',str(schema),'--output-last-message',str(output),'--json']
    return cmd+['-']
