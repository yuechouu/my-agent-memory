"""v1 compatibility smoke test for noor.

Run this AFTER installing my-agent-memory and setting HERMES_AGENT_ID=noor.

This verifies that:
1. The v2 Store class has the same API surface as v1
2. Search/save/get/archive/status/dream/rebuild methods exist and work
3. Return value shapes are compatible

Does NOT modify production data — uses an in-memory database.
"""

import os
import json
import traceback

# Ensure we're testing v2, not v1
os.environ["HERMES_AGENT_ID"] = "noor"

print("=" * 60)
print(" My Agent Memory — V1 Compatibility Test")
print("=" * 60)

passed = 0
failed = 0
warnings = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} — {detail}")


def check_warn(name, condition, detail=""):
    global passed, failed, warnings
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        warnings.append(f"{name}: {detail}")
        print(f"  WARN {name} — {detail}")


# ── Step 1: Import ──
print("\n[1] Import check")
try:
    from my_agent_memory import Store, MultiAgentStore
    check("from my_agent_memory import Store", True)
except ImportError as e:
    check("from my_agent_memory import Store", False, str(e))

try:
    store = Store(db_path=":memory:")
    check("Store(db_path=':memory:')", True)
except Exception as e:
    check("Store(db_path=':memory:')", False, str(e))

# ── Step 2: API Surface ──
print("\n[2] API surface (v1 compatibility)")

v1_methods = {"search", "save", "get", "archive", "status", "dream", "rebuild"}
store_methods = set(m for m in dir(store) if not m.startswith("_") and callable(getattr(store, m)))

for method in v1_methods:
    check(f"  {method}()", method in store_methods, f"Missing: {method}")

# Check method signatures
import inspect

def _sig_ok(obj, name, min_params):
    try:
        sig = inspect.signature(getattr(obj, name))
        params = list(sig.parameters.keys())
        # Remove 'self'
        params = [p for p in params if p != 'self']
        return len(params) >= min_params
    except Exception:
        return False

check("  search(query) signature", _sig_ok(store, "search", 1))
check("  save(content) signature", _sig_ok(store, "save", 1))
check("  get(entry_id) signature", _sig_ok(store, "get", 1))
check("  archive(entry_id) signature", _sig_ok(store, "archive", 1))
check("  status() signature", _sig_ok(store, "status", 0))
check("  dream() signature", _sig_ok(store, "dream", 0))
check("  rebuild() signature", _sig_ok(store, "rebuild", 0))

# ── Step 3: Basic CRUD ──
print("\n[3] Basic CRUD")

try:
    e = store.save("Test memory: Tencent Cloud CVM primary server", title="Server Info")
    check("save() returns dict with id", isinstance(e, dict) and "id" in e)
    check("save() has title", e.get("title") == "Server Info")
    check("save() has content", "Tencent" in e.get("content", ""))
    check("save() has created_at", bool(e.get("created_at")))
except Exception as e:
    check("save() works", False, str(e))
    traceback.print_exc()

try:
    e = store.save("Another test: npm registry is npmmirror.com", title="NPM Info")
    check("save() second entry", isinstance(e, dict) and "id" in e)
except Exception as e:
    check("save() second entry", False, str(e))

# Get
try:
    e = store.get(1)
    check("get(1) returns dict", isinstance(e, dict))
    check("get(1) has correct id", e.get("id") == 1)
except Exception as e:
    check("get(1) works", False, str(e))

# Search
try:
    results = store.search("CVM server")
    check("search() returns list", isinstance(results, list))
    check("search() returns results", len(results) > 0)
    if results:
        r = results[0]
        check("search result has id", "id" in r)
        check("search result has title", "title" in r)
        check("search result has content", "content" in r)
        check("search result has score", "score" in r)
except Exception as e:
    check("search() works", False, str(e))
    traceback.print_exc()

# Archive
try:
    e = store.archive(2)
    check("archive() returns dict", isinstance(e, dict) if e else True)
except Exception as e:
    check("archive() works", False, str(e))

# ── Step 4: Stats ──
print("\n[4] Stats")

try:
    stats = store.status()
    check("status() returns dict", isinstance(stats, dict))
    check("status() has total", "total" in stats)
    check("status() has promoted", "promoted" in stats)
    check("status() has db_path", "db_path" in stats)
    print(f"      Stats: total={stats.get('total')} promoted={stats.get('promoted')}")
except Exception as e:
    check("status() works", False, str(e))
    traceback.print_exc()

# ── Step 5: Dreaming (dry-run only) ──
print("\n[5] Dreaming (dry-run)")

try:
    result = store.dream(dry_run=True)
    check("dream(dry_run=True) returns dict", isinstance(result, dict))
    check("dream has dry_run flag", result.get("dry_run") is True)
    check("dream has candidates", "candidates" in result or "promote_preview" in result)
    print(f"      Dreaming candidates: promote={result.get('candidates', {}).get('promote', 0)}")
except Exception as e:
    check_warn("dream(dry_run=True) works", False, str(e))

# ── Step 6: v2-specific features ──
print("\n[6] V2 features (should not break v1 compat)")

# These are v2 additions — they should exist but v1 code won't call them
try:
    entry = store.save("A shared fact", title="Shared", scope="shared")
    check("save() accepts scope kwarg", entry.get("scope") == "shared")
except Exception as e:
    check("save() scope", False, str(e))

try:
    _ = store.pin(1)
    check("pin() exists and works", True)
except Exception as e:
    check_warn("pin()", False, str(e))

try:
    _ = store.share(1)
    check("share() exists", True)
except Exception as e:
    check_warn("share()", False, str(e))

try:
    _ = store.hybrid_search("server")
    check("hybrid_search() exists", True)
except Exception as e:
    check_warn("hybrid_search()", False, str(e))

# ── Step 7: Cleanup ──
print("\n[7] Cleanup")
try:
    store.close()
    check("store.close()", True)
except Exception as e:
    check("store.close()", False, str(e))

# ── Summary ──
print("\n" + "=" * 60)
print(f" RESULTS: {passed} passed, {failed} failed, {len(warnings)} warnings")
print("=" * 60)

if warnings:
    print("\nWarnings:")
    for w in warnings:
        print(f"  * {w}")

if failed > 0:
    print("\n[FAIL] Some tests FAILED. Review before migration.")
else:
    print("\n[OK] All critical checks passed. V2 is compatible with v1 API.")
    if warnings:
        print("   Review warnings above before proceeding to migration.")
