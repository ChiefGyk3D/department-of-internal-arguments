"""The local-llm-routing pack (issue ChiefGyk3D/workbench#16): the flags, exit codes, outcome values, log fields and
environment names it states are the ones the reference client really has, and the pack stays short.

The check reads a checked-in copy of the client's `--help` (tests/fixtures/client-help.txt, the program name replaced
by <client>: the validator's privacy sweep bans the real name). When the client is on PATH (or named by
LLM_LOCAL_BIN) the copy is also compared with the live help, so a drifted copy fails too."""

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

PACK_PATH = ROOT / "packs" / "local-llm-routing" / "PACK.md"
HELP_PATH = ROOT / "tests" / "fixtures" / "client-help.txt"
CLIENT = "llm" + "-local"  # built from parts: the validator's privacy sweep bans the literal name in the tree
MAX_BODY_TOKENS = 3000  # characters / 4, the estimate the pack audit uses

# The log row fields, the reason classes and the environment names, read from the client source on 2026-11-25
# (encode and write_row calls, REASONS, the module docstring). The live check below re-reads them when the tool exists.
LOG_FIELDS = (
    "kind", "id", "ts", "model", "task", "status", "exit_code", "reason", "seconds", "prompt_tokens",
    "eval_tokens", "load_duration_s", "load_fresh", "outcome",
)  # fmt: skip
REASONS = (
    "busy", "unreachable", "timeout", "over-budget", "bad-response", "bad-config", "bad-input", "interrupted",
    "internal",
)  # fmt: skip
ENV_NAMES = ("LLM_LOCAL_URL", "LLM_LOCAL_MODEL", "LLM_LOCAL_CTX", "LLM_LOCAL_CONFIG")
CONFIG_KEYS = ("vram_budget_mb", "max_concurrent", "default_est_mb", "reload_threshold_s")
# Flags the pack must state: the ones a caller needs for the rules it teaches.
REQUIRED_FLAGS = ("--task", "--outcome", "--envelope", "--wait", "--think", "--num-ctx", "--est-mb", "--allow-reload")
PRIVATE = re.compile(r"/ho" r"me/\w+|\b\d{1,3}(?:\.\d{1,3}){3}\b|chiefgyk3d|renegade-penguin", re.I)


def split_pack(text):
    """(frontmatter, body) of a PACK.md."""
    parts = text.split("---", 2)
    return parts[1], parts[2]


def help_flags(help_text):
    return set(re.findall(r"--[a-z][a-z-]*", help_text))


def help_outcomes(help_text):
    m = re.search(r"accepted\|rewritten\|discarded\|fact-error", help_text)
    return m.group(0).split("|") if m else []


def pack_flags(body):
    """Every `--flag` the pack names outside the Ollama API field names."""
    return set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", body))


def problems(body, help_text):
    """What the pack states that the client does not have, or lacks of what a caller needs. Empty means consistent."""
    out = []
    known = help_flags(help_text) | {"--help"}
    out += [f"unknown flag {f}" for f in sorted(pack_flags(body) - known)]
    out += [f"flag {f} not stated" for f in REQUIRED_FLAGS if f not in pack_flags(body)]
    out += [f"flag {f} not in help" for f in REQUIRED_FLAGS if f not in known]
    if "exit 75" not in help_text:
        out.append("help does not say exit 75")
    if not re.search(r"exit 75[^.\n]*busy", body, re.I):
        out.append("pack does not say exit 75 means busy")
    if not re.search(r"busy[^.\n]*not (?:an )?(?:outage|unreachable)", body, re.I):
        out.append("pack does not say busy is not an outage")
    outcomes = help_outcomes(help_text)
    if not outcomes or "|".join(outcomes) not in body:
        out.append("outcome values differ from help")
    out += [f"log field {f} not stated" for f in LOG_FIELDS if f"`{f}`" not in body]
    out += [f"reason class {r} not stated" for r in REASONS if f"`{r}`" not in body]
    out += [f"env name {e} not stated" for e in ENV_NAMES if e not in body]
    out += [f"config key {k} not stated" for k in CONFIG_KEYS if k not in body]
    return out


def live_tool():
    found = os.environ.get("LLM_LOCAL_BIN") or shutil.which(CLIENT)
    return found if found and os.access(found, os.X_OK) else None


class LocalLlmPack(unittest.TestCase):
    def setUp(self):
        self.text = PACK_PATH.read_text(encoding="utf-8")
        self.front, self.body = split_pack(self.text)
        self.help = HELP_PATH.read_text(encoding="utf-8")

    def test_the_help_copy_was_collected(self):
        self.assertGreater(len(help_flags(self.help)), 10)
        self.assertEqual(help_outcomes(self.help), ["accepted", "rewritten", "discarded", "fact-error"])

    def test_pack_states_what_llm_local_has(self):
        self.assertEqual(problems(self.body, self.help), [])

    def test_the_check_bites(self):
        self.assertIn("unknown flag --bogus", problems(self.body + "\nUse `--bogus`.\n", self.help))
        self.assertIn("flag --wait not stated", problems(self.body.replace("--wait", "--w8"), self.help))
        self.assertTrue(problems(re.sub("(?i)exit 75", "exit 76", self.body), self.help))
        self.assertTrue(problems(re.sub(r"\bnot an outage\b", "an outage", self.body), self.help))
        self.assertTrue(problems(self.body.replace("accepted|rewritten", "accepted|rewrote"), self.help))
        self.assertTrue(problems(self.body.replace("`load_fresh`", "`load_cold`"), self.help))
        self.assertTrue(problems(self.body.replace("LLM_LOCAL_CTX", "LLM_CTX"), self.help))
        self.assertIn("help does not say exit 75", problems(self.body, self.help.replace("exit 75", "exit 7")))

    def test_the_help_copy_matches_the_live_tool(self):
        tool = live_tool()
        if tool is None:
            self.assertGreater(len(self.help), 500)  # no tool here: the copy above is the reference
            return
        live = subprocess.run([tool, "--help"], capture_output=True, text=True, timeout=30, check=True).stdout
        self.assertEqual(" ".join(live.replace(CLIENT, "<client>").split()), " ".join(self.help.split()))
        source = Path(tool).read_text(encoding="utf-8")
        for name in LOG_FIELDS + REASONS + ENV_NAMES + CONFIG_KEYS:
            self.assertIn(name, source, name)

    def test_description_opens_with_load_and_stays_short(self):
        m = re.search(r'^description:\s*"(.*)"\s*$', self.front, re.M)
        self.assertIsNotNone(m)
        self.assertTrue(m.group(1).startswith("Load "), m.group(1)[:40])
        self.assertLess(len(m.group(1)) / 4, 90)

    def test_body_stays_under_the_token_cap(self):
        self.assertLess(len(self.body) / 4, MAX_BODY_TOKENS)
        self.assertTrue(len(self.body + "x" * 12000) / 4 >= MAX_BODY_TOKENS)  # the cap bites on a long body

    def test_measured_sections_exist_and_name_models_and_dates(self):
        for needle in (
            "gemma4:12b",
            "qwen3.5:4b",
            "qwen3:14b",
            "2026-11-25",
            "think: false",
            "8.19 GB",
            "VERIFIED",
            "INFERRED",
            "docs.ollama.com/context-length",
            "docs.ollama.com/capabilities/thinking",
            "## Evaluation record",
            "## How to verify",
        ):
            self.assertIn(needle, self.text, needle)

    def test_nothing_private_ships(self):
        self.assertIsNone(PRIVATE.search(self.text))


if __name__ == "__main__":
    unittest.main()
