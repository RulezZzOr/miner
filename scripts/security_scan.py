"""Fail publication on private runtime paths, common credential signatures or
non-English text.

This bounded scanner is a publication guard, not a claim that every secret or
vulnerability can be recognized. It prints locations and categories, never values.
"""
import hashlib
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

PRIVATE_NAMES = {"agent.toml", "ssh-targets.json", "id_rsa", "id_ed25519"}
PRIVATE_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
# Studio's owner access key is a random token that matches no content pattern,
# so a copied key file is recognised by its name or by a remediation folder.
PRIVATE_NAME_PARTS = ("access-key", "access_key")
PRIVATE_DIRS = {".switch-agent", ".apodex"}

# English-only guard. The application, its documentation and its tests are
# English; upstream benchmark data and golden fixtures keep their own text.
# Characters are escaped so this file stays plain ASCII.
LANGUAGE_EXEMPT_PREFIXES = ("frontier/benchmarks/", "frontier/tools/golden/", "analysis/", "dist/")
LANGUAGE_SUFFIXES = {
    "", ".py", ".md", ".js", ".cjs", ".mjs", ".html", ".css", ".json", ".toml", ".yaml",
    ".yml", ".sh", ".cmd", ".command", ".txt", ".cfg", ".ini", ".j2",
}
CZECH_LETTERS = re.compile("[\u011b\u0161\u010d\u0159\u017e\u016f\u0165\u010f\u0148\u00fd"
                           "\u011a\u0160\u010c\u0158\u017d\u016e\u0164\u010e\u0147\u00dd]")
# Common Czech words, compared after accents are removed. They are stored as
# SHA-256 prefixes (the same scheme as studio/tests/test_language_policy.py) so
# this guard contains no Czech text itself.
CZECH_WORD_DIGESTS = frozenset({
    '007f5eee8172', '0204a10702fd', '03357475c36c', '0474693397b9', '053efc15eb54', '07023f5ffaa9',
    '0c442ecc8f0b', '12c6c54fae28', '12e389b50a95', '1426c9088cef', '15b20e0ad5d0', '17150f7a65b8',
    '1a3bcd099b86', '1abc9225ee13', '1b17746f7827', '1d4a97e20792', '1d5071332265', '1da6ca362685',
    '1e301c3b0f7e', '2458fd882f0d', '2743e3d563c8', '27dbadfc4cb9', '27fef74ad30b', '2be1b965e6d1',
    '2c62e43828a0', '2c96f75b2822', '2e577c7b0888', '2e89cf63a321', '2ff246f3dfb5', '2ffe317df468',
    '302d78064d7f', '31f7a65e3155', '377dfe36df8a', '3a961bc9badc', '3c8869c28622', '3f3b08eca62c',
    '3f6daeb8bcbe', '4164e8dd0ac9', '416902672a96', '416cf2d81949', '42ccc6380840', '4399ec23e0c5',
    '46ba24c10112', '47a6c3737cbe', '487123b0dc9b', '4bcb041c2725', '509f649fae44', '53444ea69218',
    '5440218000f1', '546d5d37d81d', '547a905cde28', '56b1db8133d9', '59409029a2f7', '5eb59fe89054',
    '5f219f352318', '61f56b52c7bf', '62c8e1491ca0', '68415e19e298', '68652da40a81', '6d7c544ba5d3',
    '6f8c84a475e2', '6fe2a0f5118f', '715e9a5ca5df', '72df0b94e775', '73b5e7a83751', '744ac4c7a298',
    '77748b471507', '782b23b8165d', '7a04984f03bf', '7a309d1d1c65', '7b75fe788506', '7c8351cf0813',
    '7dbf60684260', '7ef455edf4ce', '7f1b9e583aac', '80900cb01986', '827ca2234851', '8483f9f5b7b0',
    '84abd1909aeb', '8585c16116f4', '8636914c0bb7', '8a0fedfd02f8', '8a6294b74f5a', '8edb9fb0ab7a',
    '902bca887148', '91f3a8e81960', '9458cd424e2f', '9571cb7e5ce9', '96a933cbfb58', '96b71cd581cc',
    '98b0fc3c220e', '9c9910c2aa68', '9f4dc16bafb9', 'a07120eb64b5', 'a2af72482315', 'a3564fb4524a',
    'a43a7f557364', 'a4488dba99c4', 'a60849ff6997', 'a78b3492514f', 'a7ea64de8e58', 'acbae14ebe29',
    'ad0293f50a43', 'ad7d6378f660', 'ae7cd24de46d', 'affe552d7c20', 'b289fb948d2f', 'b3a18d0a6f64',
    'b4bcdf8858cb', 'b617c0f11259', 'b7277fd853f5', 'b7cebae23b90', 'b8c2ab735827', 'b9f17311084b',
    'bc12b84bc41c', 'c1e9b147f024', 'c2c874b1a440', 'c4e34d0a07c8', 'c97eb22fb71c', 'caeeb47556bb',
    'cb8930ba0c14', 'cbe91cfdf6c5', 'd14f69b3218c', 'd6849fa3274d', 'd688cfff7e90', 'd6c7c63b0f7c',
    'd8305a064cd0', 'ddbd551dabcb', 'df96057690f7', 'e677184865f2', 'e7af250a8e04', 'f628421129af',
    'f75bb5a0503c', 'fa606d989abe', 'fb222d5172e0', 'ff90c910a50c',
})

PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "OpenAI-style key": re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{40,}\b"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def scan_files(root, paths):
    findings = []
    for path in paths:
        parts = tuple(p.casefold() for p in Path(path).parts)
        name = Path(path).name.casefold()
        if (root / path).is_symlink():
            findings.append({"path": path, "kind": "tracked symlink"})
            continue
        if (name in PRIVATE_NAMES or name.endswith(PRIVATE_SUFFIXES)
                or any(part in name for part in PRIVATE_NAME_PARTS)
                or any(p in PRIVATE_DIRS for p in parts)
                or (len(parts) > 2 and parts[0] == "analysis" and "remediation" in parts[2:-1])
                or (name.startswith(".env") and "example" not in name)):
            findings.append({"path": path, "kind": "private runtime path"})
            continue
        raw = (root / path).read_bytes()
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(raw):
                findings.append({"path": path, "line": raw[:match.start()].count(b"\n") + 1, "kind": kind})
    return findings


def scan_language(root, paths):
    """Report Czech letters and accent-free Czech words in shipped text files."""
    findings = []
    for path in paths:
        relative = Path(path).as_posix()
        if (relative.startswith(LANGUAGE_EXEMPT_PREFIXES) or Path(path).suffix.casefold() not in LANGUAGE_SUFFIXES
                or (root / path).is_symlink()):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if CZECH_LETTERS.search(line):
                findings.append({"path": path, "line": number, "kind": "non-English text (Czech letters)"})
            elif any(czech_word(w) for w in re.findall(r"[a-z]+", ascii_folded(line))):
                findings.append({"path": path, "line": number, "kind": "non-English text (Czech word)"})
    return findings


def ascii_folded(line):
    return "".join(c for c in unicodedata.normalize("NFKD", line.lower()) if not unicodedata.combining(c))


def czech_word(word):
    return hashlib.sha256(word.encode()).hexdigest()[:12] in CZECH_WORD_DIGESTS


def tracked_files(root):
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    return [p for p in paths if p and ((root / p).is_file() or (root / p).is_symlink())]


def main():
    root = Path(__file__).resolve().parents[1]
    paths = tracked_files(root)
    findings = scan_files(root, paths)
    language = scan_language(root, paths)
    for item in findings + language:
        print(f"{item['path']}:{item.get('line', 1)}: {item['kind']}")
    print(f"Publication credential scan: {len(findings)} finding(s).")
    print(f"English-only language scan: {len(language)} finding(s).")
    return bool(findings or language)


if __name__ == "__main__":
    sys.exit(main())
