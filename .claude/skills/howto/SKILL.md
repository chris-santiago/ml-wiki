---
description: "Answer wiki how-to questions and maintain the README How To section. Auto-invoked when the user asks how to do something with the wiki (commands, agents, workflows, configuration). TRIGGER when: user asks how to use wiki commands, agents, scripts, workflows, or configuration. SKIP: general programming questions, non-wiki tools, wiki content questions (use /query for those)."
---

# /howto

Answers a wiki how-to question, then checks whether the README already covers it.

## When this skill fires

This skill is auto-invoked when the user asks how to do something with the wiki — commands, agents, scripts, workflows, configuration.

**Examples:** "How do I render a page?", "What's the command for tag normalization?", "How do I add an experiment source?"

**Skip:** General programming questions, non-wiki tool questions, wiki *content* questions (those are `/query` territory).

---

## Step 1: Answer the question

Answer the user's question in chat. Draw on skills (read the relevant SKILL.md), scripts (`--help` output), agents, CLAUDE.md, and README as needed. Give a complete, practical answer.

---

## Step 2: Semantic check against README

Read the `## How To` section of `README.md`. Extract all `<summary>` lines from `<details>` blocks — these are the existing question titles.

Compare the user's question against these titles. This is a judgment call, not string matching — "how do I do a deep render?" and "How do I re-render a page with better quality?" cover the same topic.

### Path A — No match

No existing entry covers this topic. Prompt the user:

> "Want me to add this to the README's How To section?"

If the user declines, stop. If they accept, proceed to Step 3.

### Path B — Semantic match, phrasing is adequate

An existing entry covers this topic and its title is discoverable from the user's phrasing.

Remind the user:

> "This is already covered in the README under **[category]** → '*[existing question title]*'."

Done.

### Path C — Semantic match, phrasing gap

An existing entry covers this topic, but someone using the user's phrasing might not find it. The existing title doesn't surface the concept the user searched for.

Remind the user and suggest a rephrase:

> "This is already covered in the README under **[category]** → '*[old title]*'. Someone searching for '*[user's phrasing concept]*' might not find it. Want me to rephrase to '*[suggested new title]*'?"

If the user accepts, update the `<summary>` line in README.md via Edit. Done.

---

## Step 3: Category selection and insertion (Path A only)

1. Read the `###` headers under `## How To` in README.md (e.g., Adding Content, Querying & Synthesis, Maintenance & Housekeeping, Customization & Extension).

2. Pick the best-fitting category for the new entry.

3. If no category fits well, propose a new category name and confirm with the user before creating it.

4. Distill the chat answer into a concise, self-contained `<details>` block matching the existing style:

   ```markdown
   <details>
   <summary>How do I [question]?</summary>

   [Concise answer with code blocks and brief explanation.
   Match the terse style of existing entries — not a chat transcript.]

   </details>
   ```

5. Insert the new block at the end of the chosen category (before the next `###` header or the `---` that closes the How To section) using the Edit tool.

6. Report: "Added to README under **[category]**. Changes are not committed — review with `git diff README.md` when ready."
