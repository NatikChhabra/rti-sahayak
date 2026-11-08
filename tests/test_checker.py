from rti_sahayak import check
from rti_sahayak.checker import ERROR

GOOD = """To,
The Public Information Officer,
Office of the District Collector, Bilaspur, Chhattisgarh

Subject: Application under Section 6(1) of the Right to Information Act, 2005

Please provide, for the period 1 April 2024 to 31 March 2025:
1. Copies of the work order and completion certificate for the road in Ward 12.
2. The present status of my complaint No. 123, with the dates of each action taken.

I am a citizen of India. The application fee of Rs 10 is paid by Indian Postal Order No. 12A 345678.
Applicant: Asha Verma, House No 5, Sarkanda, Bilaspur 495001"""


def codes(text):
    return check(text).codes


def test_good_application_has_no_issues():
    r = check(GOOD)
    assert r.issues == []
    assert r.risk == "Low"


def test_empty():
    r = check("   ")
    assert r.codes == ["EMPTY"] and r.risk == "High"


def test_missing_pio():
    assert "NO_PIO" in codes(GOOD.replace("The Public Information Officer,", "The Collector,"))


def test_pio_variants_accepted():
    for v in ["The CPIO,", "The SPIO,", "The P.I.O.,", "Jan Soochna Adhikari,", "लोक सूचना अधिकारी,"]:
        assert "NO_PIO" not in codes(GOOD.replace("The Public Information Officer,", v)), v


def test_missing_contact():
    text = GOOD.replace("Applicant: Asha Verma, House No 5, Sarkanda, Bilaspur 495001", "Asha")
    assert "NO_CONTACT" in codes(text)


def test_email_is_enough_contact():
    text = GOOD.replace("Applicant: Asha Verma, House No 5, Sarkanda, Bilaspur 495001", "Reply to asha@example.com")
    assert "NO_CONTACT" not in codes(text)


def test_missing_fee():
    text = GOOD.replace("The application fee of Rs 10 is paid by Indian Postal Order No. 12A 345678.", "")
    assert "NO_FEE" in codes(text)


def test_fee_variants():
    for v in [
        "Fee of ₹10 paid online.",
        "IPO No. 12345 for Rs. 10 attached",
        "Demand draft attached.",
        "Fees paid online",
        "Fee paid via the RTI online portal",
    ]:
        text = GOOD.replace("The application fee of Rs 10 is paid by Indian Postal Order No. 12A 345678.", v)
        assert "NO_FEE" not in codes(text), v


def test_bpl_needs_certificate():
    base = GOOD.replace("The application fee of Rs 10 is paid by Indian Postal Order No. 12A 345678.", "I am BPL.")
    assert "BPL_NO_PROOF" in codes(base)
    assert "BPL_NO_PROOF" not in codes(base + " Copy of BPL card attached.")


def test_why_question_flagged():
    r = check(GOOD + "\n3. Why was my road not repaired?")
    issue = next(i for i in r.issues if i.code == "WHY_QUESTION")
    assert "Why was my road" in issue.quote


def test_explain_and_legality_flagged():
    assert "WHY_QUESTION" in codes(GOOD + "\n3. Please explain the delay.")
    assert "WHY_QUESTION" in codes(GOOD + "\n3. Is it legal to charge this fee?")


def test_record_request_quoting_a_why_is_fine():
    text = GOOD + '\n3. Copies of the file notings recording the reasons, in the matter of: "Why was it delayed?"'
    assert "WHY_QUESTION" not in codes(text)


def test_word_why_inside_a_request_is_fine():
    assert "WHY_QUESTION" not in codes(GOOD + "\n3. Copies of the notings explaining why the tender was cancelled.")


def test_future_question_flagged():
    assert "HYPOTHETICAL" in codes(GOOD + "\n3. When will the road be repaired?")
    assert "HYPOTHETICAL" in codes(GOOD + "\n3. What should I do next?")


def test_hindi_questions():
    assert "WHY_QUESTION" in codes(GOOD + "\n3. काम क्यों रुका?")
    assert "HYPOTHETICAL" in codes(GOOD + "\n3. सड़क कब तक बनेगी?")


def test_grievance():
    assert "GRIEVANCE" in codes(GOOD + "\nPlease take strict action against the officer.")
    assert "GRIEVANCE" in codes(GOOD + "\nKindly refund my fees.")


def test_private_body():
    r = check(GOOD.replace("Office of the District Collector", "HDFC Bank"))
    assert "PRIVATE_BODY" in r.codes
    assert "RBI" in next(i for i in r.issues if i.code == "PRIVATE_BODY").fix


def test_private_school():
    assert "PRIVATE_BODY" in codes(GOOD + "\n3. Fee structure of ABC private school.")


def test_public_sector_bank_not_flagged():
    assert "PRIVATE_BODY" not in codes(GOOD.replace("Office of the District Collector", "State Bank of India"))


def test_third_party():
    assert "THIRD_PARTY" in codes(GOOD + "\n3. Income tax returns of my neighbour.")
    assert "THIRD_PARTY" in codes(GOOD + "\n3. Mobile number of the contractor.")


def test_vague_without_period():
    text = GOOD.replace("Please provide, for the period 1 April 2024 to 31 March 2025:", "Please provide:")
    assert "VAGUE" in codes(text + "\n3. All information related to roads.")
    assert "VAGUE" not in codes(GOOD + "\n3. All information related to roads.")  # has a period


def test_oversharing():
    assert "OVERSHARING" in codes(GOOD + "\nAadhaar: 2345 6789 1234")
    assert "OVERSHARING" in codes(GOOD + "\nPAN ABCDE1234F")
    assert "OVERSHARING" not in codes(GOOD)


def test_too_long():
    r = check(GOOD + "\n" + "background " * 600)
    assert "TOO_LONG" in r.codes
    assert all(i.severity != ERROR for i in r.issues if i.code == "TOO_LONG")  # Rule 3 proviso


def test_too_many_points():
    many = "\n".join(f"{n}. Copy of record {n} for 2024." for n in range(3, 15))
    assert "TOO_MANY_POINTS" in codes(GOOD + "\n" + many)


def test_act_not_named():
    text = GOOD.replace(
        "Subject: Application under Section 6(1) of the Right to Information Act, 2005", "Subject: request"
    )
    assert "ACT_NOT_NAMED" in codes(text)


def test_life_liberty_tip():
    text = GOOD + "\n3. Copy of the arrest memo of my brother, detained on 3 Nov 2026."
    assert "LIFE_LIBERTY" in codes(text)
    assert "LIFE_LIBERTY" not in codes(text + "\nThis concerns life or liberty.")


def test_issues_sorted_by_severity_and_have_law():
    r = check("why is it like this? take action now")
    order = {"error": 0, "warning": 1, "tip": 2}
    assert [order[i.severity] for i in r.issues] == sorted(order[i.severity] for i in r.issues)
    assert all(i.law and i.fix for i in r.issues)


def test_own_itr_is_not_third_party():
    assert "THIRD_PARTY" not in codes(GOOD + "\n3. Processing status of the ITR filed by me for AY 2024-25.")


def test_private_unaided_school():
    assert "PRIVATE_BODY" in codes(
        GOOD.replace("Office of the District Collector", "ABC Public School (private unaided)")
    )


def test_hindi_custody_life_liberty():
    assert "LIFE_LIBERTY" in codes(GOOD + "\n3. मेरे पति को हिरासत में लिया गया है, गिरफ्तारी मेमो की प्रति दें।")
    assert "LIFE_LIBERTY" not in codes(GOOD + "\n3. हिरासत। यह सूचना जीवन या स्वतंत्रता से संबंधित है।")


def test_subject_line_quoting_a_question_is_fine():
    assert "WHY_QUESTION" not in codes(GOOD.replace("Subject: Application", 'Subject: "Why no road?" Application'))


def test_signature_date_is_not_a_period():
    text = GOOD.replace("Please provide, for the period 1 April 2024 to 31 March 2025:", "Please provide:")
    assert "VAGUE" in codes(text + "\n3. All information about roads.\nDate: 08-11-2026")


def test_private_company_named_in_letter_to_regulator_is_fine():
    to_trai = (
        "To,\nThe Public Information Officer,\nTelecom Regulatory Authority of India (TRAI), New Delhi\n\n"
        "Subject: RTI\n\nPlease provide copies of complaints received against Airtel in 2025."
    )
    assert "PRIVATE_BODY" not in check(to_trai).codes
    to_company = "To,\nThe Public Information Officer,\nHDFC Bank, Mumbai\n\nSubject: RTI\n\nCopies of my charges."
    assert "PRIVATE_BODY" in check(to_company).codes
