# Daily Paper Digest

A morning email with the papers that came out since the last one, filtered by
your themes, with every new paper by the authors you follow. Each paper shows
its title, authors, journal, date, full abstract and a link.

It runs free on GitHub Actions. No server and no dependencies: it uses only the
Python standard library.

## How it works

| Step | Source |
|---|---|
| New articles in 17 watch-list journals (JPO, JFM, JGR, GRL, PRF, Nature, Science…) | [Crossref](https://api.crossref.org) by ISSN |
| New preprints in `physics.ao-ph`, `physics.flu-dyn`, `nlin.CD` | [arXiv API](https://info.arxiv.org/help/api/) |
| Papers by followed authors in *any* journal or preprint server | Crossref author search + arXiv author search |
| Missing abstracts (some publishers don't deposit them) | Semantic Scholar, plus OpenAlex if you add a key |

* **Topic filter:** a paper is kept if a theme keyword appears in its **title**,
  or the theme's keywords appear **at least twice** in its abstract. One passing
  mention isn't enough.
* **Followed authors:** these are matched by ORCID where possible, which keeps
  out other people with the same name. Their papers are always included.
* **No repeats, nothing missed:** papers already emailed are recorded in
  `state/state.json`. Each run looks back 7 days, so papers that are indexed late
  still show up once.
* **Missing abstracts:** if a paper has no abstract yet, the digest waits up to
  2 days for one before judging the paper on its title alone.

## Set-up (about 5 minutes)

1. **Gmail app password.** Turn on 2-step verification for the Gmail account
   that will send the email. Then create an app password at
   <https://myaccount.google.com/apppasswords>.
2. **Repository secrets.** In the repo, go to *Settings → Secrets and variables
   → Actions → New repository secret* and add:

   | Secret | Value |
   |---|---|
   | `GMAIL_ADDRESS` | the Gmail address that sends the digest |
   | `GMAIL_APP_PASSWORD` | the 16-character app password |
   | `DIGEST_RECIPIENT` | where the digest goes (e.g. your Yale address) |
   | `CONTACT_EMAIL` | *optional.* Gives faster Crossref responses. |
   | `OPENALEX_API_KEY` | *optional.* A free key from openalex.org that fills in more missing abstracts. |

   The recipient is stored as a secret so your address isn't public in the repo.
3. **Test it.** Go to *Actions → Daily paper digest → Run workflow*. The email
   should arrive within about a minute.

After that it runs every morning at about 9:00 AM New York time.

## Changing what you get

Everything is in [`config.toml`](config.toml). You can edit it right on GitHub.

* **Themes:** add or remove keywords, or add a new `[[themes]]` block.
  `"turbulen*"` matches turbulence and turbulent. `"sea ice"` also matches
  sea-ice. Prefix a keyword with `re:` to use a regular expression.
* **Authors:** add an `[[authors]]` block. Including their ORCID (from
  orcid.org) makes matching reliable.
* **Journals:** add a `[[journals]]` block with the journal's ISSN.
* **Fewer or more papers:** `min_abstract_hits = 1` lets in any paper that
  mentions a keyword. Raising it gives a stricter filter.

## Run it locally

```bash
python3 -m digest --dry-run            # builds out/digest.html without sending
python3 -m unittest discover -s tests -t .
```

## Notes

* On a public repository, GitHub turns off scheduled workflows after 60 days
  with no commits. The daily state commit counts as activity, so this shouldn't
  happen. If it ever does, re-enable the workflow under *Actions*.
* Scheduled runs on GitHub can start 5–20 minutes late at busy times.
