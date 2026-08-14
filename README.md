# Big Tech Tea ☕

*The tea on what the big labs are publishing.*

Follow what the big AI labs publish — OpenAI, Google Research, DeepMind, Meta,
Microsoft Research, Amazon Science, Alibaba/Qwen, plus arXiv — and get a push
notification with a short structured brief when something in your field lands.

Each alert looks like this on your phone:

```
[Google DeepMind] Sparse MoE routing at scale
- Problem: Serving mixture-of-experts models is memory bound, and existing
  routers activate more experts than necessary.
- vs. prior work: Prior systems route per token without regard to load.
- What they did: We introduce BalanceRoute, which jointly optimises expert
  assignment and device placement.
- Results: Cuts activated parameters by 38% at equal quality on two benchmarks.
- Limits / next: Code is released, though gains shrink below eight experts.
```

**Set it up here:** https://SalmaRoshdy-YG.github.io/big-tech-tea

Free to run and free to use. GitHub Actions polls the labs' own feeds, the
summariser runs offline, and [ntfy](https://ntfy.sh) delivers the alert. No
server, no database, no account, no paid API.

---

## Two things are called "topic". They are unrelated.

This trips everyone up, so the code enforces the distinction:

| | What it is |
|---|---|
| `ntfy_topic` | The **channel string** you invent and subscribe to in the ntfy app. It's the address your alerts are delivered to, and it works like a password. |
| `interests` | The **research subjects** you want alerts about — `agentic`, `quantization`, `protein folding`. |

Your `ntfy_topic` is never derived from your interests, and changing your
interests never changes where alerts go.

---

## Two ways to use it

**A. Fork it.** Your fork, your Actions runner, your channel. Nobody else can
see it. This is the private option and the one to pick if you're unsure.

**B. Subscribe on the shared repo.** Use the
[site](https://SalmaRoshdy-YG.github.io/big-tech-tea) to generate your config and
open a PR. Convenient, but this repository is public — read
[Your channel is a password](#your-channel-is-a-password) first.

---

## Quickstart

```bash
git clone https://github.com/SalmaRoshdy-YG/big-tech-tea
cd big-tech-tea
pip install -r requirements.txt

python -m bigtechtea.cli check                 # are all the feeds alive?

cp subscriptions/_example.yml subscriptions/$(whoami).yml
$EDITOR subscriptions/$(whoami).yml

python -m bigtechtea.cli preview               # what would I have received?
python -m bigtechtea.cli run --seed            # mark the backlog as read
python -m bigtechtea.cli run                   # for real
```

Then subscribe to your channel in the ntfy app and enable Actions on your fork.
`.github/workflows/poll.yml` runs every three hours.

`run --seed` matters: without it, your first real run delivers everything
currently sitting in the feeds, all at once.

---

## Writing a subscription

`subscriptions/yourname.yml`:

```yaml
subscriptions:
  - name: agents-and-reasoning
    ntfy_topic: 7f3a9c1e04b7d2e8        # openssl rand -hex 12
    interests:
      any_of: [agentic, tool use, chain of thought]
      none_of: [hiring, internship]
    lookback_days: 7
    digest: false
```

One file can hold several subscriptions with different channels — useful for
routing "interrupt me" and "read on Sunday" to channels with different phone
notification settings.

### Interest rules

| Key | Meaning |
|---|---|
| `any_of` | Matches if **at least one** term appears |
| `all_of` | Every term must appear |
| `none_of` | Veto: if any term appears, skip the paper |
| `regex` | Full Python regex, case-insensitive |
| `content` | `paper` for arXiv papers only, `blog` for announcements, `any` for both (default) |
| `sources` | Restrict to these lab slugs (`python -m bigtechtea.cli list`) |
| `title_only` | Match the title only, ignoring the abstract |

Terms match on word boundaries, so `rag` fires on "RAG-based retrieval" but not
on "sto**rag**e". Multi-word terms match as phrases. At least one of `any_of`,
`all_of` or `regex` is required — a subscription that matches everything is
almost always a mistake.

### Delivery options

| Key | Default | Meaning |
|---|---|---|
| `priority` | 3 | ntfy priority, 1–5 |
| `digest` | false | One bundled message per run instead of one per paper |
| `lookback_days` | 7 | Ignore anything older than this |
| `max_per_run` | 15 | Cap so a feed reindex can't flood your phone |
| `server` | ntfy.sh | Your own ntfy instance |
| `token_env` | — | **Name** of the env var holding your ntfy token, never the token |

---

## How the briefs are made

Two backends. The default costs nothing and needs no network.

**`heuristic` (default).** Splits the abstract into sentences and sorts them
into the five slots by cue phrases — "prior work relies on…" is a comparison,
"we introduce…" is the method, "14% improvement" is a result. It reorganises
the authors' own words.

Be clear about what this is: it does not understand the paper. It cannot tell
you how the work differs from prior literature if the abstract doesn't say so —
and when the abstract doesn't, it says *"abstract doesn't compare to prior
work"* rather than inventing a comparison. Lab blog posts are announcements, not
abstracts, so those get a plain `In brief` line instead of a fake structure. On
a well-written arXiv abstract it works well; that's the case it was built for.

**`llm` (opt-in).** Any OpenAI-compatible `/chat/completions` endpoint:

```bash
export SUMMARIZER=llm
export LLM_BASE_URL=https://your-provider.example/v1
export LLM_MODEL=some-small-model
export LLM_API_KEY=...
python -m bigtechtea.cli run
```

Several providers offer free tiers that comfortably cover this workload, and a
local [Ollama](https://ollama.com) server works too (`LLM_BASE_URL=http://localhost:11434/v1`)
if you'd rather nothing leave your machine. Set the same values as repository
variables plus one secret to enable it in CI.

Costs are bounded three ways: briefs are cached by paper in
`state/summaries.json` so each paper is summarised at most once ever,
`LLM_MAX_CALLS` (default 25) caps calls per run, and any error or rate limit
falls back to the heuristic instead of failing the run.

Either way the model only ever sees the title and abstract. Treat a brief as a
filter for deciding what to open, not as a substitute for reading the paper.

---

## Your channel is a password

On public `ntfy.sh` there are no accounts on a channel: **anyone who knows the
string can read your alerts and publish to them.** Committing one to a public
repo publishes it permanently, including in the git history.

- Generate something unguessable: `openssl rand -hex 12`. The validator rejects
  obvious ones like `papers` or `arxiv`, but it can't judge a weak one.
- Don't reuse a channel you use for anything else, especially home automation
  or monitoring alerts.
- Your keyword list is public too, and it says something about what you or your
  group are working on. If that's sensitive, fork instead.
- To keep your channel out of the repo entirely, use `ntfy_topic_env:` with the
  name of a GitHub Actions secret instead of `ntfy_topic:`. Your keywords stay
  public; the delivery channel does not. Add a matching line to `poll.yml`.
- For real privacy: self-host ntfy with auth, set `server:` and
  `token_env: NTFY_TOKEN`, and keep the token in Actions secrets. Or fork this
  repo privately.

---

## Commands

| Command | Does |
|---|---|
| `run` | Fetch, match, summarise, notify, save state |
| `run --dry-run` | Everything except sending and saving |
| `run --seed` | Mark current matches as delivered, silently. Use on first setup |
| `preview` | Dry run ignoring saved state — "what would I have gotten?" |
| `digest` | Rebuild `docs/data/latest.json` for the site, without notifying |
| `check` | Fetch every source and report which are broken |
| `list` | Show configured labs and active subscriptions |

Add `--only openai deepmind` to any of them to limit which sources are hit, and
`--summarizer llm` to override the backend for one run.

---

## The website

`docs/` is a single static page, served by GitHub Pages straight from the
`main` branch (Settings → Pages → Source: `main`, folder: `/docs`). It has no
backend: the subscription builder runs entirely in your browser and hands off to
GitHub's "new file" flow to open your PR, and the digest below it reads
`docs/data/latest.json`, which the workflow regenerates on each run.

---

## Adding a lab

Edit `sources.yaml`:

```yaml
  - slug: some-lab
    org: Some Lab
    kind: feed                     # or: arxiv
    url: https://somelab.example/blog/rss.xml
    url_must_match: "/research/"   # optional: skip non-research posts
    title_must_not_match: "webinar|hiring"
```

For arXiv use `kind: arxiv` with a `query` in
[arXiv API syntax](https://info.arxiv.org/help/api/user-manual.html), e.g.
`cat:cs.CL` or `all:"Google DeepMind"`. Affiliation queries match anywhere in
the metadata, so expect false positives.

Run `python -m bigtechtea.cli check` before opening the PR.

**Stay a good citizen.** This project polls published feeds, which is what feeds
are for. Keep new sources to feeds or documented APIs, respect `robots.txt` and
site terms, and don't shorten the cron interval. Scraping sites that offer no
feed is out of scope: it breaks constantly and gets everyone blocked.

---

## How deduplication works

`state/seen.json` records what each subscription has been sent, and the workflow
commits it back after each run.

- Nobody gets the same paper twice.
- A new subscriber gets the last `lookback_days` of matches on their first run,
  rather than nothing or a year of backlog.
- URLs are canonicalised before hashing, so `arxiv.org/pdf/2501.00001`,
  `.../abs/2501.00001v3` and `...?utm_source=rss` are one paper.
- Entries older than 120 days are pruned.

The workflow's `concurrency` group stops two runs from racing on the push.

---

## Papers vs blog posts

Sources come in two kinds, and `python -m bigtechtea.cli list` labels them
`PAPER` or `blog`:

- **PAPER** — arXiv. One source per lab, plus optional whole-category
  firehoses (`arxiv-cs-cl`, `cs-lg`, `cs-cv`, `cs-ai`) that are off by default.
- **blog** — the labs' own sites. Useful, but these are announcements: launches,
  product news, the occasional research write-up.

If you only want papers, put `content: paper` in your `interests`. That is the
right default for most research students.

One honest caveat about the per-lab arXiv sources: arXiv has no affiliation
field, so a query like `all:"OpenAI"` searches titles, abstracts, author names
and comments. It finds papers that name the lab, misses ones that don't, and
picks up papers merely citing it. It gets you roughly the right set, not exactly
it. If you'd rather have precision on subject than on employer, enable the
category firehose for your field and lean on your keywords instead.

## Known limitations

- Lab blogs announce a fraction of what their researchers publish, and arXiv
  affiliation matching is imprecise for the reasons above.
- Feed summaries are often truncated marketing copy, so matching on abstracts is
  weaker than it looks. `title_only: true` is sometimes more honest.
- Keyword matching has no notion of meaning: it won't find a relevant paper that
  uses different vocabulary than yours. Deliberate — it keeps this free,
  deterministic and debuggable.
- Scheduled GitHub Actions are best-effort and can be delayed, and Actions on
  public repos are disabled after 60 days of repository inactivity.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT.
