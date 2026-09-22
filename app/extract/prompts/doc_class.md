You decide whether a file is a regulatory compliance document for a medical
device, and if so which kind.

You are shown the first pages of one file. Answer with one class and a short
verbatim quote from the text that justifies it.

## Classes

- `DoC` — a Declaration of Conformity. The manufacturer declares, in its own
  name, that a product meets a directive or regulation. Usually names MDR
  2017/745 or MDD 93/42/EEC, lists article or REF numbers, and is signed.
- `EC` — a certificate issued TO the manufacturer BY a notified body
  (TÜV, DEKRA, BSI, DNV, ...). Carries a certificate number and a four-digit
  notified-body number.
- `ISO` — an ISO 13485 or ISO 9001 certificate for the manufacturer's quality
  system. Also issued by a body, but about the company, not a device.
- `IFU` — instructions for use accompanying a product.
- `SPP` — a summary of safety and clinical performance.
- `NOT-A-COMPLIANCE-DOCUMENT` — anything else.

## What `NOT-A-COMPLIANCE-DOCUMENT` covers

Answer this whenever the file is not one of the five above, including:

- **A web page.** Navigation menus, cookie notices, product category lists,
  "Resources" or "Downloads" landing pages, search results. These reach you
  because a server answered a `.pdf` URL with HTML and it was rendered to
  pages. Repeated menus across pages are the clearest tell.
- **An operation manual or user guide.** These are long, have chapters and a
  table of contents, and legitimately cite MDR 2017/745 somewhere. Citing a
  regulation does not make a manual a declaration. An IFU is short and
  accompanies one product or family; a 300-page service manual is not an IFU.
- **A catalogue, price list, brochure or marketing PDF**, even one listing
  correct article numbers.
- **A safety data sheet**, an invoice, a delivery note, a purchase order.
- **A product information or specification sheet.**

## How to decide

Read what the document says it is, in its own heading, on its own first pages.
Do not infer the class from a phrase mentioned in passing: a manual that
contains the words "declaration of conformity" in a chapter list is still a
manual. If the file does not announce itself as one of the five classes, the
answer is `NOT-A-COMPLIANCE-DOCUMENT`.

When genuinely torn between two compliance classes, pick the more specific one
and say so in the quote. When torn between a compliance class and
`NOT-A-COMPLIANCE-DOCUMENT`, choose `NOT-A-COMPLIANCE-DOCUMENT`: a document
wrongly admitted becomes a fabricated compliance record, while one wrongly
refused is reviewed by a person.

`quote` must be text copied from the document, at most 15 words. If the file
contains no usable text at all, answer `NOT-A-COMPLIANCE-DOCUMENT` with an
empty quote.
