# What and why

I started from a ChatGPT-made starter with twelve personas (chief of staff, QA, security architect, and so on). Some of it was good machinery and I kept that: the installer, the policies, the behavioral exercises. But I was already running a smaller design that I had measured, three process roles plus domain packs chosen per dispatch, and twelve personas change too many variables at once to ever tell which one helped. If a review gets better, I want to know whether it was the role, the pack, or the model, and I cannot know that when the persona list is the thing that changed.

So there are seven roles, and they describe how work gets done, not what the work is about (see [Roles](roles.md)). And eight packs, which are the domain knowledge you name in a brief when the task needs it (see [Packs](packs.md)).

## The fifth persona

The sixth persona is the one dispatching the other five. It is not an agent file, because an agent that can dispatch agents is exactly what the validator forbids; it is [templates/lead.md](../templates/lead.md), installed into the controller session's instructions (`install.py --lead`; nothing loads it by itself). It says how to brief, how to read a report, when the adversarial pass is mandatory, when to stop a fix loop, and that pushes and approvals never happen below it. See [Writing a brief](writing-a-brief.md).

## One report header, one severity scale

Every role's report opens with the same header (role, model, head, base, gate, other gates, packs loaded, tree, not verified, and a `LOCAL-LLM:` line saying whether a local model was used, which exact model answered, what its output was checked against and how many discrepancies were found), so the lead can log a task from the header alone and a hook can check `MODEL:` against the dispatch. Both reviewers use one severity scale: blocking, important, minor.

## What I did not keep

Three more things from that starter I did not keep, and the reasons are measured or at least argued in [the pilot notes](../evals/pilot-2026-10.md):

- `model: inherit`: an inherited model can quietly become the expensive one, so every agent names its model.
- Reviewers with no shell: a reviewer that cannot run the gate or revert a fix cannot prove the fix bites.
- "Only the human runs tests": the implementer runs its own gate and reports the exit code.
