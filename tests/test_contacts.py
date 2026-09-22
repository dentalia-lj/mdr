"""Triage of e-mail addresses found in compliance documents.

Every fixture here is a REAL address measured out of `document_text` on
2026-09-03, not an invented one -- the point of the classifier is the messy
cases, and inventing them would test a cleaner world than the corpus is.
"""

import pytest

from app import contacts as c

# authored playbook domains, verbatim from playbooks/*.json on 2026-09-03
VOCO = ["voco.dental"]
KOMET = ["brasseler.de"]
HENRY_SCHEIN = ["henryschein.de"]
EDENTA = ["edenta.com"]
DENTSPLY = ["dentsplysirona.com"]
MEDENTIKA = ["cad.medentika.com", "straumann.com/medentika"]
GC = ["gc.dental/europe"]

OTHERS = {
    "ZHERMACK": ["zhermack.com"],
    "VITA": ["vita-zahnfabrik.com"],
    "VALOC": ["valoc.ch"],
}


# --------------------------------------------------------------------------- #
# the registrable label is what makes a loose match possible
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("domain,label", [
    ("voco.de", "voco"),
    ("voco.dental", "voco"),          # authored domain and mail domain differ
    ("de.tuv.com", "tuv"),            # subdomain must not become the label
    ("cad.medentika.com", "medentika"),
    ("galit.te.ua", "galit"),         # two-label public suffix
    ("henryschein.com", "henryschein"),
    ("edenta.ch", "edenta"),
])
def test_registrable_label(domain, label):
    assert c.registrable_label(domain) == label


def test_a_playbook_domain_carrying_a_path_still_yields_its_host_label():
    # `gc.dental/europe` and `straumann.com/medentika` are authored with paths.
    v = c.classify("info@gc.dental", GC)
    assert v.verdict == c.MANUFACTURER


# --------------------------------------------------------------------------- #
# the loose match: authored domain != the domain they send mail from
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("address,domains", [
    ("info@voco.de", VOCO),                    # authored voco.dental
    ("marketing@voco.com", VOCO),
    ("a.novak@voco.de", VOCO),
    ("info@brasseler.de", KOMET),              # KOMET's legal entity
    ("cbdeurope@henryschein.com", HENRY_SCHEIN),  # authored henryschein.de
    ("info@edenta.ch", EDENTA),                # authored edenta.com
])
def test_a_manufacturers_own_address_is_kept(address, domains):
    v = c.classify(address, domains)
    assert v.verdict == c.MANUFACTURER, v.reason


def test_exact_domain_matching_would_have_rejected_voco():
    # The mutation guard for the whole loose-match design. If `classify` ever
    # compares full domains again, this is the test that catches it: VOCO's
    # authored domain is voco.dental and every real address is voco.de/.com.
    assert "voco.de" not in VOCO
    assert c.classify("info@voco.de", VOCO).verdict == c.MANUFACTURER


# --------------------------------------------------------------------------- #
# third parties: present in the document by necessity, never the maker
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("address", [
    "certificate.verification@bsigroup.com",   # BSI, notified body
    "medcert-info@dnv.com",                    # DNV
    "products@de.tuv.com",                     # TUV Rheinland
    "medical@tuev-nord.de",                    # TUV Nord
    "usa@tuv-nord.com",
    "medical.devices@dqs-med.de",              # DQS
    "certifiering@ri.se",                      # RISE
    "dice@fda.hhs.gov",                        # US regulator
    "prastaff@fda.hhs.gov",
    "anovak@medicept.com",                     # regulatory consultancy
])
def test_a_certifier_or_regulator_is_never_a_contact(address):
    v = c.classify(address, VOCO, OTHERS)
    assert v.verdict == c.THIRD_PARTY, v.reason


def test_third_party_wins_over_an_own_domain_collision():
    # Ordering guard: if a manufacturer ever authored a certifier's domain,
    # that is a data problem to see, not a contact to write.
    v = c.classify("info@bsigroup.com", ["bsigroup.com"])
    assert v.verdict == c.THIRD_PARTY


# --------------------------------------------------------------------------- #
# the interesting class: a document naming somebody else's company
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("address,own,expected_owner", [
    ("info@zhermack.com", DENTSPLY, "ZHERMACK"),
    ("info@vita-zahnfabrik.com", DENTSPLY, "VITA"),
    ("info@valoc.ch", MEDENTIKA, "VALOC"),
])
def test_another_manufacturers_address_is_named_not_written(address, own, expected_owner):
    v = c.classify(address, own, OTHERS)
    assert v.verdict == c.OTHER_MANUFACTURER
    assert v.matched == expected_owner
    assert "misfiled" in v.reason


# --------------------------------------------------------------------------- #
# unknown: real-looking, no authored domain to check it against
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("address", [
    "contact@anthogyr.com",    # ANTHOGYR has no playbook
    "info@bredent.com",        # nor does BREDENT
    "office@galit.te.ua",      # a distributor? nobody knows
    "info@duerr.cn",           # duerr != duerrdental; plausible, unproven
])
def test_an_unrecognised_domain_goes_to_a_human(address):
    v = c.classify(address, ["duerrdental.com"], OTHERS)
    assert v.verdict == c.UNKNOWN


def test_a_manufacturer_with_no_authored_domains_writes_nothing():
    # 2 of 38 playbooks carry no `domains`, and 346 of 384 manufacturers have
    # no playbook at all. Silence is the right answer, never a guess.
    assert c.classify("info@anything.com", []).verdict == c.UNKNOWN
    assert c.classify("info@anything.com", None).verdict == c.UNKNOWN


# --------------------------------------------------------------------------- #
# extraction and ranking
# --------------------------------------------------------------------------- #

def test_addresses_are_found_deduplicated_and_lowercased():
    text = "Write to INFO@Voco.de or info@voco.de, cc a.novak@voco.de."
    assert c.find_addresses(text) == ["info@voco.de", "a.novak@voco.de"]


def test_a_trailing_full_stop_is_not_part_of_the_address():
    assert c.find_addresses("Contact info@voco.de.") == ["info@voco.de"]


def test_no_text_finds_nothing():
    assert c.find_addresses("") == []
    assert c.find_addresses(None) == []


def test_role_mailboxes_rank_above_people():
    # A renewal request sent to info@ still arrives after the named person
    # leaves, which is the whole reason this ordering exists.
    vs = [c.classify(a, VOCO) for a in
          ("b.horvat@voco.de", "info@voco.de", "a.novak@voco.de",
           "dealersupport@voco.de")]
    assert [v.address for v in c.rank(vs)] == [
        "dealersupport@voco.de", "info@voco.de",
        "a.novak@voco.de", "b.horvat@voco.de",
    ]


def test_every_verdict_is_in_the_declared_vocabulary():
    for a, d in (("info@voco.de", VOCO), ("dice@fda.hhs.gov", VOCO),
                 ("info@zhermack.com", DENTSPLY), ("x@nowhere.test", VOCO)):
        assert c.classify(a, d, OTHERS).verdict in c.VERDICTS


# --------------------------------------------------------------------------- #
# personal vs functional: the rule that decides what a playbook may carry
# --------------------------------------------------------------------------- #
# Denis, 2026-09-03: "personal out. dealersupport might be, keep it." The
# distinction is NOT an allowlist of role words -- `cbdeurope@henryschein.com`
# and `marketing@voco.com` are on no such list and are plainly departmental,
# and an allowlist would have left HENRY SCHEIN with no address at all.

@pytest.mark.parametrize("local", [
    "jane.doe",           # firstname.lastname
    "a.novak",            # initial.surname
    "b.horvat",
    "peter_k",            # firstname_initial
])
def test_an_individuals_mailbox_is_personal(local):
    assert c.looks_personal(local) is True


@pytest.mark.parametrize("local", [
    "info", "contact", "dealersupport",
    "cbdeurope",          # a business unit, on no role list
    "marketing",          # functional, on no role list
    "customer.service",   # two parts, but both function words
    "medical.devices",
    "certificate.verification",
])
def test_a_functional_mailbox_is_not_personal(local):
    assert c.looks_personal(local) is False


def test_playbook_writable_drops_people_and_keeps_functions():
    voco = ["voco.dental"]
    vs = [c.classify(a, voco) for a in (
        "info@voco.de", "a.novak@voco.de", "dealersupport@voco.de",
        "b.horvat@voco.de", "marketing@voco.com")]
    kept = [v.address for v in c.playbook_writable(vs)]
    assert kept == ["dealersupport@voco.de", "info@voco.de", "marketing@voco.com"]


def test_playbook_writable_drops_certifiers_and_unknowns_too():
    # `manufacturer` AND not personal -- nothing else may reach a playbook.
    vs = [c.classify(a, ["voco.dental"], {"ZHERMACK": ["zhermack.com"]})
          for a in ("medcert-info@dnv.com", "info@zhermack.com",
                    "contact@anthogyr.com", "info@voco.de")]
    assert [v.address for v in c.playbook_writable(vs)] == ["info@voco.de"]


def test_henry_schein_keeps_its_only_address():
    # The regression this rule exists for: an allowlist of role words would
    # have dropped `cbdeurope@` and left this manufacturer unaddressable.
    vs = [c.classify(a, ["henryschein.de"]) for a in
          ("cbdeurope@henryschein.com", "jane.doe@henryschein.com")]
    assert [v.address for v in c.playbook_writable(vs)] == ["cbdeurope@henryschein.com"]
