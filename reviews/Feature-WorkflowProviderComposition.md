# Code review - Feature/WorkflowProviderComposition

**Review status:** `in-progress`
**Reviewed up to commit:** `ddadb2ef9eb6180502e07ce0d270de45a2419ac3` `(2026-10-14)`
**Security-reviewed up to commit:** `ddadb2ef9eb6180502e07ce0d270de45a2419ac3` `(2026-10-14)`
**Judgment:** `changes-requested`

```agent-host-coverage
{"schema_version": 1, "candidate_head": "ddadb2ef9eb6180502e07ce0d270de45a2419ac3", "shared_source": ".agents/azure/utility/scaffold/scripts/scaffold.py", "hosts": [{"host": "claude", "behavior": "Compose the shared pure workflow writer with Azure infrastructure and immutable asset deployment in one transaction", "source": ".agents/azure/utility/scaffold/scripts/scaffold.py", "mapping": ".claude/skills/scaffold and plugins/azure/skills/scaffold", "verification": {"level": "source", "result": "passed", "evidence": "Frozen canonical script, cloud runtime, options and infrastructure readme byte-match all generated mirrors; focused real-writer suite and generated freshness passed. Source verification only; authoritative full Terraform/lint matrix remains remote."}}, {"host": "codex", "behavior": "Compose the shared pure workflow writer with Azure infrastructure and immutable asset deployment in one transaction", "source": ".agents/azure/utility/scaffold/scripts/scaffold.py", "mapping": ".codex/skills/scaffold and plugins/azure/skills/scaffold", "verification": {"level": "source", "result": "passed", "evidence": "Frozen canonical script, cloud runtime, options and infrastructure readme byte-match all generated mirrors; focused real-writer suite and generated freshness passed. Source verification only; authoritative full Terraform/lint matrix remains remote."}}]}
```

## Review pass - 2026-10-14 - full

**Candidate base:** `08bd29969ae4aedbfcb801e9157d1783fa8d4e4b`
**Candidate head:** `ddadb2ef9eb6180502e07ce0d270de45a2419ac3`
**Candidate branch:** `Feature/WorkflowProviderComposition`
**Candidate scope:** `all`
**Candidate path-set:** `sha256:9647a40eed9523f599825985bd832347a25a17705b14cbb3314bb6710cd43caa` `(24 paths)`
**Candidate bundle:** `C:\Users\TommySeery\AppData\Local\Temp\review\cgBDAhly3SZl92RuDj1w72\HosT932owxFuDZyfwQP4QE\wNnzs7ZoWYodxoIuJXtfCy`
**Candidate bundle identity:** `sha256:67e87cd908d6effc2726a42610584470501a550e30f1cf8820dfa61447e4c4d5`
**Work-order path:** `reviews/Feature-WorkflowProviderComposition.md`
**Work-order mode:** `new`
**Pass judgment:** `changes-requested`

Descriptor: `C:\Users\TommySeery\source\repos\tj-agents\azure\.git\agent-workflow\runs\standards-azure-provider-20261014\review\c0d9f3b3b668598a1dc6822e257b5f0b248e8f411fcd62f02f679047f32fa11f\descriptor.json`. Identity: `69c8b0fdc2cfdd05f67370e5358616a2be760665ae3841bfbb4083978468ff81`.
Patch: `sha256:df7ade2ede3af774faf2d30bf5f91f18882726a00be9091b3deed4a3d0519cfb`. Exactly one initial synchronization.
Fresh native-general substitutes for unavailable native CLI review. Azure API/contract correctness and
L2 security independently inspect the same immutable end-to-end provider cutover. Source writer stopped.
Parent alone verifies evidence, assigns severity and judgment. One-sitting full review: one coherent
provider boundary, fewer than300 paths and10k authored diff lines; generated mirrors are checked for parity.
No reviewer receives sibling conclusions or executes cloud, Terraform or application builds.

### Findings

- [~] **AP-1 - MEDIUM - native correctness** - `.agents/azure/utility/scaffold/scripts/scaffold.py:1057`
  New deployment_config consumes Function probes without validating their object/path shape. Nonobject
  probes pass profile validation then raise uncaught AttributeError; invalid readiness paths are written
  and fail only in generated runtime. Validate consumed optional probes/readiness before any output,
  preserve valid omitted-probe defaults, and cover profile-to-runtime malformed inputs.
- [~] **AP-2 - MEDIUM - native/API deadline** - `.agents/azure/utility/scaffold/resources/azure_deploy.py:253`
  wait_for counts30 sleeps and excludes probe time; thirty20-second probes consume900 seconds while
  reporting a five-minute deadline. Azure subprocesses have no timeout. Enforce a monotonic300-second
  deadline, cap probe subprocess timeouts and sleeps to remaining budget, and cover virtual elapsed time
  and TimeoutExpired propagation. The removed shell loops also counted attempts; retained here because
  this newly shipped unified runtime explicitly promises bounded five-minute waits and must conform to
  this candidate's accepted serving/migration contract, rather than treating old timing as approval.
- [~] **AP-3 - MEDIUM - Azure/API serving** - `.agents/azure/utility/scaffold/resources/azure_deploy.py:345`
  Ingress readiness refuses its HTTP request until the revision is Running/Healthy. The generated
  min_replicas defaults to zero, so an idle same-digest revision cannot receive the request that wakes it.
  Verify expected current image/revision and routed traffic, issue the bounded serving request, then
  require that same revision to be active/Running/Healthy and successful HTTP; background checks remain.
  Cover a zero-scale revision transitioning to serving and wrong-image/old-revision/traffic negatives.

Parent synthesis: all three independent full v2 results validate identity, exact seven locators,
referential anchors and acceptance conditions. Security found no further concrete introduced defect.
Native/API deadline evidence deduplicated as AP-2. Parent frozen in-memory probe confirms AP-1/AP-2,
artifact a484fd5fcea2b557-provider-parent-findings.log; frozen state gates and default-zero component
confirm AP-3's request ordering, with primary scaling/API evidence supplied independently. No cloud run.

### Validation gate outside changed Azure source

Exact-head Terraform38545025023 scaffold Linux/Windows and Kit38545025914 both OS/hosts passed.
Emitted Actions Lint failed on ci.yml constant true build condition from immutable shared writer e00dfd.
That source pre-exists outside Azure changed paths; canonical GHA BUGS.md#B002 owns the bounded repair,
record-only branch Docs/OidcBuildConditionRecord at8fb9fdd and source Fix/OidcBuildCondition. No Azure fake
verification, generic condition rewrite or lint suppression is permitted. Azure adopts its immutable
merged source after review/CI/delivery. Completed Azure review remains truthful at ddadb2e; source repair
requires a fresh incremental pass and replacement exact-head CI before merge.

## Publication scope assessment

The sole preflight route was delivery-assess-scope. Parent resolves it inline under git-branching:
this standalone item0 caller cutover is recorded as PR not opened in the canonical goal, with no
existing same-branch PR or stack owner (fresh forge query empty). New review targets main and covers
exact ddadb2ef9eb6180502e07ce0d270de45a2419ac3. Measured24 paths/8681 diff lines fit one sitting;
8 authored paths form one atomic provider transaction/runtime/output-name boundary. Identity and
published dependency metadata are unchanged and retain their later release gate. No open platform-sync
PR was found. Code committed, base current, frozen mirror/host checks and full private audit passed.
Draft publication starts authoritative CI while three independent full lenses remain pending; it
confers no merge approval. Existing user authorization and standing reviewed-green delivery apply.


## Review pass - 2026-10-14 - incremental

**Candidate base:** `ddadb2ef9eb6180502e07ce0d270de45a2419ac3`
**Candidate head:** `02e98dfed35002d81c70b94a6b1bc7da3176fbea`
**Candidate branch:** `Feature/WorkflowProviderComposition`
**Candidate scope:** `all`
**Candidate path-set:** `sha256:74356cc3d687a3c098016842557d822548c1d6f1709d5b9987d2b696f17844f0` `(17 paths)`
**Candidate bundle:** `C:\Users\TommySeery\AppData\Local\Temp\review\cgBDAhly3SZl92RuDj1w72\HosT932owxFuDZyfwQP4QE\xSfY58DwLG53CAjU4KOLce`
**Candidate bundle identity:** `sha256:ecbbe64db8f9d6520f8a03ee23271f1aa1110418401ac2b86c307f025c0889f9`
**Work-order path:** `reviews/Feature-WorkflowProviderComposition.md`
**Work-order mode:** `append`
**Pass judgment:** `pending`

Descriptor: `C:\Users\TommySeery\source\repos\tj-agents\azure\.git\agent-workflow\runs\standards-azure-provider-20261014\review\c527d8e7c0f02c6e770808d4e0a38b71e12ef9f4a725b60c15502baf8aa423f6\descriptor.json`. Identity: `dc0dda49a2d22d0f285fa0a5b4323c2dcfa28dcc8e655c7f9e653130701389c7`.
Patch: `sha256:28dba86e06ddca210cebeda1cc1f6aead8f9eaad7034f07075994929c77aeefb`. No repeat synchronization.
Source writer stopped;193 authored repair lines plus parent immutable two-spot GHA432ba03 binding
and source-owner goal evidence. Parent174 real-writer tests/12 existing skips pass;52 focused tests,
240 freshness/AST/parity/whitespace and tracked-package Kit pass. Fresh native-general fallback first,
then Azure/API workflow and L2 security concerns cover only this immutable delta. Original full pass
judgment, MEDIUM severities and prior candidate identity/watermarks remain until delta completes.

### Findings

Review in progress.
