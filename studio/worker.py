"""Start a runner only after Studio has durably recorded its process identity."""
import json
import tomllib
import os
import sys
import time
from pathlib import Path


def record_setup_failure(directory, exc):
    """Leave the controller a reason when the worker fails before the runner starts."""
    try:
        outcome = {"status": "failed", "reason": f"Worker setup failed: {exc}"[:2000], "failure_kind": "setup"}
        temporary = directory / "result.tmp"
        temporary.write_text(json.dumps(outcome, ensure_ascii=False))
        temporary.replace(directory / "result.json")
    except OSError:
        pass


def main():
    directory = Path(sys.argv[1])
    parent = os.getppid()
    deadline = time.monotonic() + 30
    while not (directory / "activated").exists():
        if os.getppid() != parent or time.monotonic() >= deadline:
            return 1
        time.sleep(0.05)
    if os.getppid() != parent:
        return 1
    try:
        command = prepare(directory)
    except Exception as exc:
        record_setup_failure(directory, exc)
        raise
    try:
        os.execve(command[0], command, clean_environment(os.environ))
    except OSError as exc:
        record_setup_failure(directory, exc)
        raise


def clean_environment(environ):
    try:
        from studio.isolation import clean_environment as clean
    except ImportError:
        from isolation import clean_environment as clean
    return clean(environ)


def prepare(directory):
    request = json.loads((directory / "request.json").read_text())
    try:
        from studio.isolation import isolated_command
        from studio.access import private_file
    except ImportError:
        from isolation import isolated_command
        from access import private_file
    if request.get("backend") == "codex" or request.get("oauth_provider"):
        raise RuntimeError("OAuth execution needs an isolated credential broker; host credential mounting is disabled. Select an API/local model.")
    directory = directory.resolve()
    request["config"] = str(Path(request["config"]).resolve(strict=True))
    # Supply only the selected provider key, never the controller's whole dotenv file.
    from dotenv import dotenv_values
    with Path(request['config']).open('rb') as stream:
        profile = tomllib.load(stream)['profiles'][request['profile']]
    needed = profile.get('api_key_env') if profile.get('auth', 'env') == 'env' else None
    values = dotenv_values(request['env_file']) if needed else {}
    key = os.environ.get(needed) or values.get(needed) if needed else None
    private_env = directory/'provider.env'
    private_file(private_env, (needed+'='+json.dumps(key)+'\n') if needed and key else '')
    request['env_file'] = str(private_env)
    # Scope provider credentials and review evidence before entering isolation.
    scoped = directory/'request.json'
    scoped.write_text(json.dumps(request))
    argv = [sys.executable, '-u', str(Path(__file__).with_name('runner.py').resolve()), str(directory)]
    readonly = [scoped, Path(request['config']), private_env]
    if request.get('review_objects'):
        # Expose only immutable objects named in this attempt, not all company evidence.
        objects = directory/'review-objects'; objects.mkdir(mode=0o700)
        for digest in {x['sha256'] for x in request['mission']['review_packet']['sources'].values()}:
            if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest): raise ValueError('Invalid review object')
            (objects/digest).write_bytes((Path(request['review_objects'])/digest).read_bytes())
        request['review_objects']=str(objects);scoped.write_text(json.dumps(request));readonly.append(objects)
    data = request.get('controller_data')
    return isolated_command(argv, request['cwd'], writable=[directory], readonly=readonly,
                            protected=[data] if data else [], managed_root=data)


if __name__ == "__main__":
    raise SystemExit(main())
