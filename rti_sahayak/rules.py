"""The legal facts RTI Sahayak relies on, in one place, each with its source.

Scope: the Right to Information Act, 2005 and the Central RTI Rules, 2012
(central public authorities). States have their own fee rules; see README "Limits".
"""

ACT = "Right to Information Act, 2005"
RULES = "Right to Information Rules, 2012"

# Fees and length (RTI Rules, 2012)
APPLICATION_FEE_RS = 10  # Rule 3
WORD_LIMIT = 500  # Rule 3: "ordinarily" not more than 500 words, excluding annexures;
# the proviso says no application can be rejected only for being longer.
PAGE_FEE_RS = 2  # Rule 4: per A4/A3 page
INSPECTION_FREE_HOURS = 1  # Rule 4: first hour free, then Rs 5 per hour
FEE_MODES = [
    "Indian Postal Order",
    "Demand Draft",
    "Banker's cheque",
    "Cash (with receipt)",
    "Online (rtionline.gov.in)",
]  # Rule 6

# Time limits (RTI Act, 2005)
REPLY_DAYS = 30  # s.7(1)
LIFE_LIBERTY_HOURS = 48  # s.7(1) proviso
APIO_EXTRA_DAYS = 5  # s.5(2) read with s.7(1): filed through an Assistant PIO
THIRD_PARTY_DAYS = 40  # s.11(3): when third-party information is involved
TRANSFER_DAYS = 5  # s.6(3): wrong authority must transfer within 5 days
FIRST_APPEAL_DAYS = 30  # s.19(1): from reply, or from the date the reply was due
FAA_DECISION_DAYS = 30  # s.19(6): first appeal decided in 30 days...
FAA_DECISION_MAX_DAYS = 45  # ...or 45 with written reasons
SECOND_APPEAL_DAYS = 90  # s.19(3): to the Central/State Information Commission

REF = {
    "s2f": "s.2(f) RTI Act: 'information' means material held on record",
    "s2h": "s.2(h) RTI Act: only 'public authorities' are covered",
    "s6_1": "s.6(1) RTI Act: request in writing, with the prescribed fee, to the PIO",
    "s6_2": "s.6(2) RTI Act: no reasons or personal details needed except to contact you",
    "s6_3": "s.6(3) RTI Act: PIO must transfer to the right authority within 5 days",
    "s7_1": "s.7(1) RTI Act: reply within 30 days; 48 hours if life or liberty is involved",
    "s8_1_j": "s.8(1)(j) RTI Act: personal information of others can be withheld",
    "s19": "s.19 RTI Act: first appeal in 30 days, second appeal in 90 days",
    "rule3": "Rule 3, RTI Rules 2012: Rs 10 fee; ordinarily up to 500 words",
    "rule5": "Rule 5, RTI Rules 2012: no fee for BPL applicants with a certificate copy",
    "aditya": "CBSE v. Aditya Bandopadhyay (2011) 8 SCC 497: PIOs need not give opinions, "
    "reasons or interpretations that are not already on record",
}
