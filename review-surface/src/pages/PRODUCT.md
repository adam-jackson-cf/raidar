# Product

## Users

Raidar supports experimentation and measurement of e2e feature delivery with agents. Users create scenarios that represent real delivery work, run harness and model pairs (agents) against stable scenario contracts, inspect evidence and use those findings to decide what to run next.

Users are usually in an investigative workflow rather than a passive reporting workflow. They need to move from a zoomed out lens into specific detail, so the next experiment (scenario revision) can be based on evidence instead of anecdotes.

## Product Purpose

Raidar helps delivery teams answer which approach works best for a specific delivery task, why it performed that way, and what should be tested next. The product turns experiment runs, and evidence into a decision path that supports assessing agent selection, surrounding practices and scaffolding that contribute to an iterative improvement in delivery outcomes.

The core loop is: create a reusable scenario baseline with a stable scoring contract, run one or more agents, compare performance and reliability, inspect failures and evidence, select one evidence-backed improvement hypothesis, create exactly one comparable revision, and run the revision to determine whether the change improved, tied, regressed, or remained inconclusive.

Success means users can confidently choose a scenarios approach and agent combination for their delivery context and can improve an agent run through scenario revisions. A simple path might be revision 1 with prompt instructions only, followed by revision 2 that uses the evidence from revision 1 to add behavioural guidance in an AGENTS.md file and test whether the target metric improves. A more elaborate approach may involve adding custom linting or bespoke deterministic tooling that guides agent behaviour.

## Anti-references

Avoid noisy, confusing use of data and signals. Do not bury users in undifferentiated charts, shallow rankings, or visual density that obscures the next decision.

Avoid generic SaaS dashboard polish, decorative AI gradients, hero-metric templates, analytics theater, vague "insight" copy, and any design that treats benchmark scores as self-explanatory. The UI must not imply confidence where sample adequacy, validity, gates, diffs, or run evidence do not support it.

Avoid flows that jump directly to run details without first establishing the broader context.

## Design Principles

1. Start zoomed out, then earn the detail. let users narrow into evidence without losing orientation.
2. Guide the next question. Every view should help users decide what to compare, what changed, what failed, what evidence matters, or what revision hypothesis is worth testing next.
3. Treat evidence as the source of authority. Aggregate views should lead to detail explains the result.
4. The ux should be:
  1. Consistency Through Repetition Is Paramount
  
    The same action, object, status, wording, visual semantic, and operation must carry the same meaning across the app. A user should expect the same result when taking or observing the same action throughout the product.
  2. Visual Language Is a Semantic System
  
    Colour, shape, position, density, motion, iconography, and typography are not decoration. They simplify and reinforce meaning: state, priority, category, ownership, affordance, containment, workflow order, and task mode.
  3. Flow State Comes From Natural Progression
  
    The UX should not rely on explanatory copy to make the task understandable. Start from a zoomed-out state that surfaces signals of interest, provide an obvious path to zoom in, reveal supporting detail, and preserve a return path.
  4. Information Architecture Is a Promise
  
    Navigation, object hierarchy, page hierarchy, taxonomy, search, filters, and cross-links must match the user's mental model. A feature that supports a key job but is buried or mislabeled creates an IA contradiction.
  5. Feature Surface Contracts Govern Screens
  
    Every wireframed surface must express a feature contract: purpose, user problem, data, content, actions, states, permissions, errors, dependencies, and evidence gaps.
  6. Operation Grammar Must Be Predictable
  
    Click, tap, hover, focus, select, open, edit, save, cancel, undo, retry, dismiss, and destructive actions must behave consistently. If one operation has different consequences, the control form must make that unmistakable.
  7. Consider data sizing impact on Ui
  
    UI choices need to be suitable for the amount of data surfaced, labels are no going without interpretation if the label text is too long, navigation links or buttons are no good if we expect high volume of nav items etc.  Understand the data source before selecting an appropriate format.
5. The dashboard should help users move from "what happened" to "what should we test next".

## Accessibility &amp; Inclusion

Target WCAG 2.2 AA. The product should be keyboard-first, screen-reader navigable, reduced-motion safe, and resilient for color-blind users. Charts and status signals must not rely on color alone; labels, shapes, ordering, and text summaries should carry the same meaning.