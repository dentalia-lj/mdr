"""A built PDF page carrying one annotation whose appearance is written by hand.

The case is VOCO's e-signed declarations (Certifaction): the signature field's
appearance paints an IMAGE, and the issue date ("Cuxhaven, 14.01.2026") is
printed only inside it, so no text layer holds it. PyMuPDF cannot sign, and its
widget API writes an appearance of its own, so the objects are written directly
here, as the signing service writes them: a signature field with a signature
value (`/V`; MuPDF draws an unsigned one as "SIGN"), its normal appearance a
form XObject. Used by `test_pdf.py` and `test_tiers_text.py`.
"""

from __future__ import annotations

import pymupdf

SIGNED_AT = "Cuxhaven, 14.01.2026"

#: Where the annotation sits on the page (top-left coordinates), clear of the
#: text the test documents write from the top.
BOX = pymupdf.Rect(72, 600, 322, 660)


def text_png(text: str) -> bytes:
    """`text` rendered into a PNG: printed on the page, in no text layer."""
    src = pymupdf.open()
    page = src.new_page(width=BOX.width, height=BOX.height)
    page.insert_text((8, 36), text, fontsize=16)
    return page.get_pixmap(dpi=96).tobytes("png")


def annotate(doc: pymupdf.Document, page_no: int | None = None, *,
             appearance: str | None = "image", subtype: str = "Widget",
             hidden: bool = False, text: str = SIGNED_AT) -> pymupdf.Document:
    """`doc` with one annotation on page `page_no` (1-based; the last page by
    default), saved and opened again so MuPDF reads it as a file it was given.

    `appearance`: "image" paints `text` as an image (the VOCO case); "text"
    writes `text` with a font, which MuPDF's text extraction reads; None gives
    the annotation no appearance (an invisible signature). `subtype` "Widget"
    is a signed signature field, "Stamp" a stamp annotation. `hidden` sets the
    Hidden flag, under which a viewer does not draw it."""
    page_no = page_no or doc.page_count
    width, height = BOX.width, BOX.height
    ap = None
    if appearance == "image":
        tmp = doc.new_page()
        image = tmp.insert_image(tmp.rect, stream=text_png(text))
        ap = doc.get_new_xref()
        doc.update_object(ap, f"<</Type/XObject/Subtype/Form/BBox[0 0 {width} {height}]"
                              f"/Resources<</XObject<</Stamp {image} 0 R>>>>>>")
        doc.update_stream(ap, f"q {width} 0 0 {height} 0 0 cm /Stamp Do Q".encode())
    elif appearance == "text":
        font = doc.get_new_xref()
        doc.update_object(font, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
        ap = doc.get_new_xref()
        doc.update_object(ap, f"<</Type/XObject/Subtype/Form/BBox[0 0 {width} {height}]"
                              f"/Resources<</Font<</Helv {font} 0 R>>>>>>")
        doc.update_stream(ap, f"BT /Helv 12 Tf 8 24 Td ({text}) Tj ET".encode())

    page = doc[page_no - 1]
    top = page.rect.height
    rect = f"[{BOX.x0} {top - BOX.y1} {BOX.x1} {top - BOX.y0}]"
    flags = 4 | (2 if hidden else 0)                     # Print, and Hidden if asked
    shown = f"/AP<</N {ap} 0 R>>" if ap else ""
    annot = doc.get_new_xref()
    if subtype == "Widget":
        value = doc.get_new_xref()
        doc.update_object(value, "<</Type/Sig/Filter/Adobe.PPKLite/SubFilter/ETSI.CAdES.detached"
                                 "/M(D:20260114140407+00'00')>>")
        doc.update_object(annot, f"<</Type/Annot/Subtype/Widget/FT/Sig/T(sig)/F {flags}"
                                 f"/Rect{rect}/P {page.xref} 0 R/V {value} 0 R{shown}>>")
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm", f"<</Fields[{annot} 0 R]/SigFlags 3>>")
    else:
        doc.update_object(annot, f"<</Type/Annot/Subtype/{subtype}/F {flags}/Rect{rect}"
                                 f"/P {page.xref} 0 R{shown}>>")
    doc.xref_set_key(page.xref, "Annots", f"[{annot} 0 R]")
    if appearance == "image":
        doc.delete_page(doc.page_count - 1)              # the page the image was made on
    return pymupdf.open("pdf", doc.tobytes())
