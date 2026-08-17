---
status: accepted
date: 2026-08-17
---

# The app ships its own Python engine

Grader's grading engine is Python. Until issue #23 the app shipped none of it: at launch,
`find_engine_dir()` searched six locations for a **source checkout** of this repository —
the build machine's `CARGO_MANIFEST_DIR`, the working directory, three `$HOME` guesses, and
one developer's absolute path. It then ran `scripts/start_engine.sh`, which began
`cd /Users/laccd/code/grader` and required `uv` on the `PATH`.

On the machine that built it, candidate six always matched. On any other machine all six
missed, the failure went to stderr — invisible from Finder — and the window waited on a
spinner forever. 0.1.1 and 0.1.2 were both structurally incapable of running anywhere else.

We now bundle the engine. `Grader.app/Contents/Resources` carries a relocatable CPython, the
project's runtime dependencies, and the `engine/` source; `engine.rs` resolves all three from
the bundle's resource directory and consults nothing else.

## The decisions

1. **A resource directory, not a packaged binary.** PyInstaller would have to be taught about
   docling, torch, and transformers — hidden imports and data files that fail at runtime rather
   than at build time. A plain directory is inspectable, and `scripts/verify_engine_bundle.py`
   can start it exactly as the app does.

2. **A relocatable CPython from uv's managed store**, copied whole. A Homebrew or system
   interpreter records absolute paths and would put the dependency on the build machine back.
   `scripts/build_engine_bundle.py` refuses to bundle anything with a `pyvenv.cfg`, which is how
   the first attempt at this silently packaged the project's own virtualenv.

3. **Dependencies installed with `--target`, reached by `PYTHONPATH`.** No virtualenv is
   involved, so there is no `home` key pointing back at where it was built.

4. **The size is accepted.** The bundle is ~1.1 GB, from `docling[standard]`: torch (418 MB),
   OpenCV (120 MB), scipy (70 MB), transformers (46 MB). Grader was 12 MB. The alternative is
   `docling-slim`, which drops the layout and OCR models and changes what the product can read
   from a scanned PDF — a grading-quality decision, not a packaging one, and not one to make
   inside a packaging fix. **If the download size proves to be a barrier for teachers, that
   trade is the thing to revisit, and it needs its own evidence.**

5. **Failure is stated in the window, never only on stderr.** `get_engine_info` returns the
   engine's own `error_kind`, `error_message`, `error_detail`, and log path; `EngineUnavailable`
   renders them with a retry. A spinner that never resolves is not an acceptable way to report
   that something is broken.

6. **The hardened runtime needs entitlements.** Python `dlopen`s its extension modules and
   torch allocates executable memory, so `app/src-tauri/Entitlements.plist` sets
   `disable-library-validation`, `allow-jit`, and `allow-unsigned-executable-memory`. Signing
   walks every Mach-O under `Resources` individually — `codesign --deep` would re-sign the
   interpreter without them, and imports would then be refused.

## What this does not settle

The generalisation from #23 is that **every check that runs where the source lives is blind to
whether the product can leave it.** `scripts/verify_engine_bundle.py` narrows that gap — it
relocates the bundle outside the checkout, strips `uv` and Homebrew from `PATH`, hands the
engine a throwaway `HOME`, and asks `lsof` whether the running process has any file open inside
this repository. It runs on the build machine, so it is a fast check, not proof.

The proof is `foreign_machine_first_run` in the clean-machine evidence: the signed app launched
on a Mac that has never held this repository. Nothing that runs on the build machine can stand
in for it, and a fresh user profile explicitly cannot — that is what produced the 0.1.1 evidence
this ADR exists to correct.
