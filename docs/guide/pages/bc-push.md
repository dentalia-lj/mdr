# Business Central

**In one sentence:** what we would tell Business Central about each article,
shown to you before we tell it.

**Status:** Live. Checked against the code on 2026-09-11.

---

## Can I break anything here?

No.

- Looking never sends. Opening the page only shows you a list.
- **Send** does not talk to Business Central either. It shows you what it is
  about to queue and asks you to confirm before it does anything; even after
  you confirm, it hands the work to the system, which does it in the
  background.
- The line at the top of the page tells you plainly whether sending is
  switched on. When it is off — the normal setting — the page still works
  out what would change; it just does not reach Business Central.
- Nothing here changes a document, a link, or anything in this registry. The
  only thing that can change is three fields on an article's card in Business
  Central.
- Sending the same thing twice is harmless: the system compares what Business
  Central already holds and sends only what differs. Pressing Send when nothing
  has changed sends nothing.
- If a value turns out to be wrong, correct the underlying document here and
  press Send again. The next send overwrites it.

---

## What this is

Three fields on each article's card in Business Central:

- **Valid declaration of conformity** — do we hold a declaration that covers
  this article?
- **Valid CE certificate** — do we hold a notified-body certificate that covers
  this article?
- **Warehouse link** — a link from Business Central back to this article's page
  here, so anyone looking at the card can see the actual documents.

## What "covers this article" means

The document has to cover *the article itself, or a group of articles it
belongs to*. A document that covers only the manufacturer does not count.

That distinction is the whole point. A certificate saying "this company's
quality system is approved" is a statement about the supplier. It is not
evidence about the specific article a customer is asking about, and putting it
into Business Central as though it were would tell a colleague something we
cannot back up.

The date matters too. A document past its own stated expiry, or past the expiry
of the certificate it relies on, does not count. A document with no expiry
printed on it is fine — most declarations do not carry one.

## Articles you will not see here

Articles the pipeline has never processed are not listed and are never sent.

A tick box has only two positions and neither of them means "we have not looked
yet". If we sent "no" for an article nobody has examined, a colleague reading
the card would reasonably conclude we checked and found nothing. Leaving those
articles alone is the honest answer.

## When you use it

Rarely, and you do not have to. A daily background job works through the
catalogue on its own, oldest first, so Business Central stays current without
anyone pressing anything.

Use this page when you want a specific change to arrive now — for example after
approving a batch of documents that a colleague is waiting on.

## Before you start

Nothing.

## What you do

1. Click **Business Central push** in the **Operator** block at the bottom of
   the menu.
2. Read the top of the page: whether sending is switched on at all, and — if
   there are more than 1000 articles that could change — how many there
   really are, since the list below only ever shows the first 1000.
3. Read the heading below that: how many articles would change, and how many
   already match.
4. Look down the list. Each row shows one article, one field, and the value
   that would be sent. "First send" means we have never told Business Central
   anything about that article.
5. If it looks right, press **Send**. It asks you to confirm how many
   articles you are about to queue, and reminds you whether sending is
   actually switched on. Press **Yes, send them** to go ahead, or **Cancel**
   to back out without queueing anything.
6. The page confirms how many articles were queued.

To send one article on its own, open that article's page instead and press
**Update Business Central** there.

## What happens then

- The work is queued, not done. It happens in the background, usually within a
  minute or two.
- Come back to this page: articles that were sent successfully move into the
  "already match" count and drop off the list.
- An article that stays on the list after several minutes did not send. That is
  worth telling a developer about — most often it means Business Central does
  not have a card for that article at all.
- At most 1000 articles are listed at a time. When there are more, the note
  at the top of the page says so and gives the real total. Nothing is left
  out; the rest arrive through the daily job.
