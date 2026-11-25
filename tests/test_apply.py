"""Tests for assisted apply. The rules are plain Python; one test drives a real browser."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from opencareer import apply
from opencareer.apply import Field
from opencareer.models import JobPosting

PROFILE = {
    "name": "Alex Rivera", "email": "alex@example.com", "phone": "408-000-0000",
    "links": {"linkedin": "https://linkedin.com/in/example-alex", "github": "https://github.com/alex"},
    "preferences": {"needs_sponsorship": False, "willing_to_relocate": True},
    "education": {"school": "UC Berkeley", "graduation": "2026-06"},
    "application_answers": {
        "work_authorized_us": True, "start_date": "Immediately",
        "how_did_you_hear": "Company careers page",
        "address": {"street": "123 Main St", "city": "San Jose", "state": "California",
                    "zip": "95113", "country": "United States"},
        "demographics": {"gender": "Decline to answer", "veteran_status": "I am not a protected veteran"},
    },
}
ANSWERS = apply.answers_from_profile(PROFILE)


def test_answers_from_profile():
    assert (ANSWERS["first_name"], ANSWERS["last_name"]) == ("Alex", "Rivera")
    assert ANSWERS["work_authorized"] == "Yes"
    assert ANSWERS["sponsorship"] == "No"
    assert ANSWERS["salary"] is None                                # always left for you


def test_unset_yes_no_answers_stay_unknown():
    answers = apply.answers_from_profile({"name": "A B"})
    assert answers["sponsorship"] is None and answers["work_authorized"] is None


@pytest.mark.parametrize("label, key", [
    ("First Name *", "first_name"), ("Last Name", "last_name"), ("Email Address", "email"),
    ("Resume/CV", "resume_file"), ("Cover Letter", "cover_letter_file"),
    ("Will you now or in the future require sponsorship?", "sponsorship"),
    ("Are you legally authorized to work in the United States?", "work_authorized"),
    ("Desired salary", "salary"), ("How did you hear about this job?", "how_heard"),
    ("Are you Hispanic/Latino?", "hispanic_latino"), ("Veteran Status", "veteran_status"),
    ("Why do you want to work here?", None),
    ("Address *", "address_line1"), ("Street Address", "address_line1"), ("City", "city"),
    ("State/Province", "state"), ("ZIP Code", "zip"), ("Postal code", "zip"), ("Country *", "country"),
    ("Please state in a few sentences why you are interested in this role", None),
    ("Are you legally authorized to work in the United States?", "work_authorized"),
])
def test_classify(label, key):
    assert apply.classify(label) == key


def opts(*texts):
    return [{"id": str(i), "text": t} for i, t in enumerate(texts)]


def test_address_answers():
    assert ANSWERS["address_line1"] == "123 Main St"
    assert (ANSWERS["city"], ANSWERS["state"], ANSWERS["zip"]) == ("San Jose", "California", "95113")
    assert apply.answers_from_profile({"application_answers": {"address": {"city": "San Jose", "state": "California"}}}
                                      )["location"] == "San Jose, CA"


def test_state_and_country_options():
    assert apply.choose_option(opts("AZ", "CA", "NV"), "California")["text"] == "CA"
    assert apply.choose_option(opts("Arizona", "California"), "CA")["text"] == "California"
    assert apply.choose_option(opts("Canada", "United States", "United States Minor Outlying Islands"),
                               "United States")["text"] == "United States"
    assert apply.choose_option(opts("United States of America", "United Kingdom"),
                               "United States")["text"] == "United States of America"


def test_choose_option():
    assert apply.choose_option(opts("Select...", "Yes", "No"), "No")["text"] == "No"
    assert apply.choose_option(opts("Yes, I am authorized", "No, I am not"), "Yes")["text"] == "Yes, I am authorized"
    assert apply.choose_option(opts("Male", "Female", "Decline To Self Identify"), "Decline to answer")["text"] \
        == "Decline To Self Identify"
    assert apply.choose_option(opts("Maybe"), "Yes") is None        # no confident match -> leave for you


def test_plan_fills_known_fields_and_leaves_the_rest():
    fields = [
        Field("a", "text", "First Name *", True), Field("b", "email", "Email *", True),
        Field("c", "file", "Resume/CV *", True), Field("d", "select", "Require sponsorship? *", True, opts("", "Yes", "No")),
        Field("e", "text", "Desired salary"), Field("f", "textarea", "Why us? *", True),
    ]
    actions, needs_you = apply.plan(fields, ANSWERS, {"resume": Path("r.pdf")})
    done = {a.field_id: a.value for a in actions}
    assert done == {"a": "Alex", "b": "alex@example.com", "c": "r.pdf", "d": "No"}
    assert needs_you == ["Desired salary", "Why us? *"]


def test_apply_urls():
    def job(source, url, source_id="123"):
        return JobPosting(source=source, source_id=source_id, title="T", company="C", url=url)
    assert apply.apply_url_for(job("greenhouse", "https://coinbase.com/careers/123"), "coinbase") == \
        ("https://job-boards.greenhouse.io/coinbase/jobs/123", True)
    assert apply.apply_url_for(job("lever", "https://jobs.lever.co/plaid/abc"), "lever:plaid")[0] == \
        "https://jobs.lever.co/plaid/abc/apply"
    assert apply.apply_url_for(job("ashby", "https://jobs.ashbyhq.com/openai/x"), "ashby:openai")[0] == \
        "https://jobs.ashbyhq.com/openai/x/application"
    assert apply.apply_url_for(job("usajobs", "https://www.usajobs.gov/job/1"), None) == \
        ("https://www.usajobs.gov/job/1", False)


@pytest.mark.skipif(not os.environ.get("OPENCAREER_BROWSER_TESTS"),
                    reason="set OPENCAREER_BROWSER_TESTS=1 to run the real-browser test")
def test_fills_a_real_form_and_never_submits(tmp_path):
    from playwright.sync_api import sync_playwright

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    form = Path(__file__).parent / "fixtures" / "practice_application.html"
    with sync_playwright() as p:
        browser = apply._launch(p, headless=True)
        page = browser.new_page()
        page.goto(form.resolve().as_uri())
        status = apply.fill_page(page, ANSWERS, {"resume": resume})
        values = page.evaluate("""() => ({
            first: first_name.value, last: last_name.value, email: email.value,
            auth: auth.options[auth.selectedIndex].text, sponsor: sponsor.options[sponsor.selectedIndex].text,
            gender: gender.options[gender.selectedIndex].text, salary: salary.value, why: why.value,
            resume: resume.files.length, vet: document.querySelector('input[name=vet]:checked')?.parentNode.innerText.trim(),
            submitted: !!window.__submitted, panel: !!document.getElementById('opencareer-panel'),
            addr: addr.value, city: city.value, state: state.options[state.selectedIndex].text,
            zip: zip.value, country: country.options[country.selectedIndex].text, statement: statement.value })""")
        browser.close()
    assert values["first"] == "Alex" and values["last"] == "Rivera" and values["email"] == "alex@example.com"
    assert values["auth"] == "Yes" and values["sponsor"] == "No"
    assert values["gender"] == "Decline To Self Identify"
    assert values["vet"] == "I am not a protected veteran"
    assert values["resume"] == 1
    assert values["salary"] == "" and values["why"] == ""          # left for you
    assert "Desired salary" in status.needs_you
    assert (values["addr"], values["city"], values["state"], values["zip"]) == ("123 Main St", "San Jose", "CA", "95113")
    assert values["country"] == "United States"
    assert values["statement"] == ""                                # "Please state..." is not the State field
    assert values["submitted"] is False                              # NEVER submitted
    assert values["panel"] is True


def test_candidate_urls_try_the_greenhouse_form_first():
    job = JobPosting(source="greenhouse", source_id="8168315", title="SWE Intern", company="Coinbase",
                     url="https://www.coinbase.com/careers/positions/8168315?gh_jid=8168315")
    urls = apply.candidate_urls(job, "coinbase")
    assert urls[0] == "https://boards.greenhouse.io/embed/job_app?for=coinbase&token=8168315"
    assert urls[1] == "https://job-boards.greenhouse.io/coinbase/jobs/8168315"
    assert urls[-1] == job.url


browser_test = pytest.mark.skipif(not os.environ.get("OPENCAREER_BROWSER_TESTS"),
                                  reason="set OPENCAREER_BROWSER_TESTS=1 to run real-browser tests")
FIXTURES = Path(__file__).parent / "fixtures"


@browser_test
def test_clicks_apply_and_fills_the_form_inside_a_frame(tmp_path):
    from playwright.sync_api import sync_playwright

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    with sync_playwright() as p:
        browser = apply._launch(p, headless=True)
        page = browser.new_page()
        page.goto((FIXTURES / "careers_page_with_apply_button.html").resolve().as_uri())
        assert len(apply.find_form(page)[1]) < apply.MIN_FIELDS     # no form yet
        assert apply.click_apply_button(page) is True
        status = apply.fill_page(page, ANSWERS, {"resume": resume})
        frame = page.frame_locator("#gh")
        first = frame.locator("#first_name").input_value()
        submitted = page.frames[1].evaluate("() => !!window.__submitted")
        browser.close()
    assert status.state == "filled"
    assert first == "Alex"
    assert submitted is False


@browser_test
def test_never_clicks_a_submit_button_named_apply_and_waits_for_you():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = apply._launch(p, headless=True)
        page = browser.new_page()
        page.goto((FIXTURES / "careers_page_no_button.html").resolve().as_uri())
        clicked = apply.click_apply_button(page)
        status = apply.fill_page(page, ANSWERS, {})
        submitted = page.evaluate("() => !!window.__submitted")
        panel = page.evaluate("() => document.getElementById('opencareer-panel').innerText")
        browser.close()
    assert clicked is False and submitted is False
    assert status.state == "waiting"
    assert "Click this site's Apply button" in panel


NEW_STYLE_PROFILE = {
    **PROFILE,
    "education": {"school": "University of California, Berkeley",
                  "degree": "B.S. in Data & Information Systems", "graduation": "2026-06"},
    "experience": [{"title": "IT Support Technician", "org": "Campus IT", "dates": "2025-09 to 2025-12"}],
    "application_answers": {**PROFILE["application_answers"], "over_18": True,
                            "standard_answers": {"government_official": False}},
}


def test_employment_and_degree_answers():
    answers = apply.answers_from_profile(NEW_STYLE_PROFILE)
    assert (answers["employer"], answers["job_title"]) == ("Campus IT", "IT Support Technician")
    assert (answers["job_start_month"], answers["job_start_year"]) == ("September", "2025")
    assert answers["degree_level"] == "Bachelor's Degree"
    assert answers["discipline"] == "Data & Information Systems"
    assert answers["over_18"] == "Yes"


@pytest.mark.parametrize("label, key", [
    ("Start date month*", "job_start_month"), ("Start date year*", "job_start_year"),
    ("End date year*", "job_end_year"), ("Company name*", "employer"), ("Title*", "job_title"),
    ("When can you start?", "start_date"), ("Are you at least 18 years of age?*", "over_18"),
    ("Discipline*", "discipline"),
])
def test_employment_labels(label, key):
    assert apply.classify(label) == key


def test_school_names_match_despite_punctuation():
    options = opts("University of California - Berkeley", "University of California - Berkeley")
    assert apply.choose_option(options, "University of California, Berkeley")["text"] == \
        "University of California - Berkeley"


def test_file_inputs_found_by_id_when_label_says_attach():
    fields = [Field("f", "file", "Attach", True, hint="resume")]
    actions, _ = apply.plan(fields, ANSWERS, {"resume": Path("r.pdf")})
    assert actions[0].how == "upload"


def test_needs_you_has_no_duplicates():
    fields = [Field("a", "text", "Why us? *", True), Field("b", "text", "Why us? *", True)]
    assert apply.plan(fields, ANSWERS, {})[1] == ["Why us? *"]


@browser_test
def test_fills_greenhouse_new_style_form_without_any_submit_attempt(tmp_path):
    from playwright.sync_api import sync_playwright

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    answers = apply.answers_from_profile(NEW_STYLE_PROFILE)
    with sync_playwright() as p:
        browser = apply._launch(p, headless=True)
        page = browser.new_page()
        page.goto((FIXTURES / "greenhouse_new_style.html").resolve().as_uri())
        answers = {**apply.answers_from_profile(NEW_STYLE_PROFILE, "Acme"), "_company": "Acme",
                   "_memory": {"which of the following best describes how you use ai tools today":
                               "Daily as part of my workflow"}}
        status = apply.fill_page(page, answers, {"resume": resume})
        snapshot = page.evaluate(apply.READ_ANSWERS_JS)
        v = page.evaluate("""() => { const $ = id => document.getElementById(id); return {
            prev: $("prev").dataset.chosen, gov: $("gov").dataset.chosen, ai: $("ai").dataset.chosen,
            priv: $("priv").dataset.chosen || "", vet: $("vet").dataset.chosen || "",
            resume: $("resume").files.length, company: $("company").value, title: $("title").value,
            month: $("start_month").dataset.chosen, year: $("start_year").value, school: $("school").dataset.chosen,
            degree: $("degree").dataset.chosen, location: $("location").dataset.chosen, age: $("age").dataset.chosen,
            auth: $("auth").dataset.chosen, attempts: window.__submitAttempts }; }""")
        # Even pressing Enter while OpenCareer holds the lock can't submit:
        page.evaluate(apply.LOCK_JS)
        page.locator("#company").press("Enter")
        locked_attempts = page.evaluate("() => window.__submitAttempts")
        page.evaluate(apply.UNLOCK_JS)
        browser.close()
    assert v["resume"] == 1
    assert (v["company"], v["title"]) == ("Campus IT", "IT Support Technician")
    assert (v["month"], v["year"]) == ("September", "2025")
    assert v["school"] == "University of California - Berkeley"
    assert v["degree"] == "Bachelor's Degree"
    assert v["location"] == "San Jose, California, United States"
    assert (v["age"], v["auth"]) == ("Yes", "Yes")
    assert v["attempts"] == 0 and locked_attempts == 0
    assert "Unlabeled field" not in status.needs_you
    assert v["prev"] == "No"                                     # from your job history (never worked at Acme)
    assert v["gov"] == "No"                                      # from standard_answers
    assert v["ai"] == "Daily as part of my workflow"             # remembered answer
    assert v["priv"] == ""                                        # agreements: always you
    assert v["vet"] == "I am not a protected veteran"             # opened and picked, no typing
    assert any("Privacy Notice" in item for item in status.needs_you)
    assert "remembered" in status.filled
    labels = {item["label"]: item["value"] for item in snapshot}
    assert labels["Company name*"] == "Campus IT"


def test_previously_employed_comes_from_job_history():
    profile = {"experience": [{"org": "Initech"}, {"org": "Campus IT"}]}
    assert apply.answers_from_profile(profile, "Initech")["previously_employed"] == "Yes"
    assert apply.answers_from_profile(profile, "Coinbase")["previously_employed"] == "No"
    assert apply.answers_from_profile(profile)["previously_employed"] is None


@pytest.mark.parametrize("label, key", [
    ("Have you previously been employed by Coinbase in any capacity?*", "previously_employed"),
    ("Are you a close relative of a government official (i.e., child/step-child)?*", "relative_of_government_official"),
    ("Are you a current government official or were you a government official in the last five years?*",
     "government_official"),
    ("...that could create an actual or perceived conflict of interest?*", "conflict_of_interest"),
    ("To your knowledge, were you referred to this position by a senior leader or decision-maker?*",
     "referred_by_business_partner"),
])
def test_standard_question_labels(label, key):
    assert apply.classify(label) == key


def test_question_key_works_across_companies():
    assert apply.question_key("Why do you want to work at Coinbase?*", "Coinbase") == \
        apply.question_key("Why do you want to work at Stripe?", "Stripe") == "why do you want to work at {company}"


def test_memory_round_trip_and_agreements_never_saved():
    from opencareer import storage
    conn = storage.connect(":memory:")
    saved = apply.save_memory(conn, [
        {"label": "Which of the following best describes how you use AI tools today?*", "value": "Daily"},
        {"label": "Please confirm receipt of the Privacy Notice*", "value": "Yes"},
        {"label": "I understand Coinbase may use AI tools*", "value": "Yes"},
        {"label": "First Name*", "value": "Alex"},                          # profile covers it
        {"label": "Which team?*", "value": "Select..."},                      # not answered
    ], "Coinbase")
    assert saved == 1
    assert apply.load_memory(conn) == {"which of the following best describes how you use ai tools today": "Daily"}


def test_plan_never_fills_agreements_even_from_memory():
    fields = [Field("p", "combobox", "Please confirm receipt of the Privacy Notice*", True)]
    answers = {**ANSWERS, "_memory": {"please confirm receipt of the privacy notice": "Yes"}}
    actions, needs_you = apply.plan(fields, answers, {})
    assert actions == [] and needs_you == ["Please confirm receipt of the Privacy Notice*"]


def test_search_phrases_include_the_distinctive_part():
    assert apply._search_phrases(["University of California, Berkeley"]) == \
        ["University of California, Berkeley", "Berkeley"]
    assert apply._search_phrases(["Yes"]) == ["Yes"]


# ---------- Claude picks leftover dropdowns ----------

def test_facts_for_ai_leave_out_private_things():
    import json
    facts = apply.facts_for_ai({**NEW_STYLE_PROFILE, "location": "San Jose, CA",
                                "skills": ["Python", "Networking"]})
    text = json.dumps(facts, default=str)
    assert "education" in facts and "experience" in facts and "skills" in facts
    for private in ("alex@example.com", "408-000-0000", "123 Main", "veteran", "Decline",
                    "linkedin", "Alex Rivera", "needs_sponsorship"):
        assert private not in text


def test_only_safe_unanswered_choice_questions_go_to_claude():
    fields = [
        Field("a", "select", "How did you hear about this job?*", True, opts("LinkedIn", "Acme site")),
        Field("b", "combobox", "Discipline*", True),
        Field("c", "combobox", "Please confirm receipt of the Privacy Notice*", True),
        Field("d", "select", "Veteran Status", False, opts("I am a veteran", "I am not")),
        Field("e", "select", "Have you ever been convicted of a felony?", True, opts("Yes", "No")),
        Field("f", "select", "Will you require visa sponsorship?", True, opts("Yes", "No")),
        Field("g", "text", "Why Acme?", True),
        Field("h", "select", "Are you at least 18?", True, opts("Yes", "No")),
        Field("grp", "radio", "Preferred office", True, [{"id": "r1", "text": "NYC"}, {"id": "r2", "text": "SF"}]),
        Field("grp2", "radio", "Team", True, [{"id": "r3", "text": "Infra"}, {"id": "r4", "text": "Apps"}]),
    ]
    chosen = apply.ai_candidates(fields, done_ids=["h", "r3"])
    assert [f.id for f in chosen] == ["a", "b", "grp"]


def test_saved_answer_and_probe_words():
    answers = apply.answers_from_profile(NEW_STYLE_PROFILE)
    assert apply.saved_answer("Discipline*", answers) == "Data & Information Systems"
    assert apply.saved_answer("How did you hear about us?", answers) == "Company careers page"
    assert apply.saved_answer("Veteran status", answers) is None          # demographics never sent
    assert apply.probe_words("Data & Information Systems") == \
        ["Data & Information Systems", "Data", "Information", "Systems"]
    assert apply.probe_words(None) == []


def test_claude_can_only_pick_a_listed_choice():
    questions = [{"id": "a", "question": "Discipline*", "options": ["Computer Networking", "Other"]},
                 {"id": "b", "question": "Team?", "options": ["Infra", "Apps"]},
                 {"id": "c", "question": "Heard?", "options": ["Acme careers site", "LinkedIn"]}]
    reply = """Here you go: {"answers": [
        {"id": "a", "choice": "computer networking", "why": "closest to the major"},
        {"id": "b", "choice": null, "why": "a preference"},
        {"id": "c", "choice": "Glassdoor", "why": "made up"},
        {"id": "zzz", "choice": "Other"}]}"""
    picks = apply.parse_ai_choices(reply, questions)
    assert picks == [{"id": "a", "label": "Discipline*", "choice": "Computer Networking",
                      "kind": "fact", "why": "closest to the major"}]


def test_preference_answers_come_back_as_suggestions():
    questions = [{"id": "t", "question": "Primary team?", "options": ["Platform (SF)", "Security (NYC)"]}]
    reply = '{"answers": [{"id": "t", "choice": "Platform (SF)", "kind": "suggestion", "why": "networking, wants CA"}]}'
    assert apply.parse_ai_choices(reply, questions)[0]["kind"] == "suggestion"
    assert apply.parse_ai_choices("not json at all", questions) == []


class FakeClaude:
    """Stands in for the Anthropic client: records what was sent, returns a fixed reply."""
    def __init__(self, reply):
        self.reply, self.sent = reply, []
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.reply)])


def test_chooser_sends_questions_and_resume_facts_only(monkeypatch):
    monkeypatch.delenv("OPENCAREER_AI_PICKS", raising=False)
    client = FakeClaude('{"answers": [{"id": "a", "choice": "Acme careers site", "why": "same as saved"}]}')
    job = SimpleNamespace(title="Software Engineer", company="Acme")
    choose = apply.ai_chooser(NEW_STYLE_PROFILE, job, client=client, goals=["California", "Remote (US)"])
    picks = choose([{"id": "a", "question": "How did you hear?", "options": ["Acme careers site", "LinkedIn"],
                     "saved": "Company careers page"}])
    assert picks[0]["choice"] == "Acme careers site"
    choose([{"id": "b", "question": "Team?", "options": ["Payments", "Wallets"]}],
           [{"question": "How did you hear?", "answer": "Acme careers site"}])
    assert "Acme careers site" in client.sent[1]["messages"][0]["content"]   # earlier answers go along
    sent = client.sent[0]["messages"][0]["content"]
    assert "How did you hear?" in sent and "Data & Information Systems" in sent
    assert '"California"' in sent                                   # your target locations, best first
    for private in ("alex@example.com", "408-000-0000", "123 Main", "veteran"):
        assert private not in sent


def test_chooser_is_off_without_a_key_or_when_turned_off(monkeypatch):
    job = SimpleNamespace(title="SWE", company="Acme")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert apply.ai_chooser(PROFILE, job) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("OPENCAREER_AI_PICKS", "0")
    assert apply.ai_chooser(PROFILE, job) is None


def test_chooser_failure_just_skips(monkeypatch):
    monkeypatch.delenv("OPENCAREER_AI_PICKS", raising=False)

    class Broken:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("network down")

    choose = apply.ai_chooser(PROFILE, SimpleNamespace(title="SWE", company="Acme"), client=Broken())
    assert choose([{"id": "a", "question": "Q", "options": ["Yes", "No"]}]) == []


@browser_test
def test_claude_picks_leftover_dropdowns_and_they_are_marked_blue(tmp_path):
    import json
    from playwright.sync_api import sync_playwright

    asked, calls = [], []

    def fake_choose(questions, earlier=None):
        asked.extend(questions)
        calls.append((len(questions), [e["answer"] for e in earlier or []]))
        by_label = {q["question"]: q["id"] for q in questions}
        reply = {"answers": [
            {"id": by_label.get("Discipline*"), "choice": "Information Technology", "why": "closest to the major"},
            {"id": by_label.get("How did you hear about this job?*"), "choice": "Acme careers site", "why": "same"},
            {"id": by_label.get("What type of opportunity are you interested in?*"), "choice": "Full-time",
             "kind": "suggestion", "why": "graduated in 2026"},
            {"id": by_label.get("Which of the following best describes how you use AI tools today?*"),
             "choice": "Daily as part of my workflow", "why": "builds AI agents"},
        ]}
        return apply.parse_ai_choices(json.dumps(reply), questions)

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    answers = {**apply.answers_from_profile(NEW_STYLE_PROFILE, "Acme"), "_company": "Acme"}
    with sync_playwright() as p:
        browser = apply._launch(p, headless=True)
        page = browser.new_page()
        page.goto((FIXTURES / "greenhouse_new_style.html").resolve().as_uri())
        status = apply.fill_page(page, answers, {"resume": resume}, choose=fake_choose)
        v = page.evaluate("""() => { const $ = id => document.getElementById(id); return {
            discipline: $("discipline").dataset.chosen, heard: $("heard").selectedOptions[0].text,
            opp: [...document.querySelectorAll("[name=opp]")].find(r => r.checked)?.value || "",
            dashed: document.querySelector("[name=opp][value=ft]").style.outline,
            blue: $("discipline").style.outline, priv: $("priv").dataset.chosen || "",
            ai: $("ai").dataset.chosen || "", menu: document.getElementById("menu").children.length,
            panel: document.getElementById("opencareer-panel").innerText,
            attempts: window.__submitAttempts }; }""")
        browser.close()
    sent = {q["question"]: q for q in asked}
    assert set(sent) == {"Discipline*", "How did you hear about this job?*",
                         "What type of opportunity are you interested in?*",
                         "Which of the following best describes how you use AI tools today?*"}   # no agreements
    assert v["ai"] == "Daily as part of my workflow"                 # clicked in the open list
    assert v["menu"] == 0                                            # no menu left open afterwards
    assert {"Data Science", "Information Technology", "Other"} <= set(sent["Discipline*"]["options"])
    assert sent["Discipline*"]["saved"] == "Data & Information Systems"
    assert sent["How did you hear about this job?*"]["options"] == ["Acme careers site", "LinkedIn", "Referral"]
    assert v["discipline"] == "Information Technology" and v["heard"] == "Acme careers site"
    assert v["opp"] == "ft"                                        # Claude's suggestion
    assert "solid" in v["blue"] and "rgb(9, 105, 218)" in v["blue"]  # solid blue = from your profile
    assert "dashed" in v["dashed"]                                   # dashed blue = a suggestion
    assert v["priv"] == "" and v["attempts"] == 0
    assert "Claude answered from your profile" in v["panel"] and "Claude suggested" in v["panel"]
    assert "graduated in 2026" in v["panel"]                         # the reason is shown
    assert "Read 3 choices" in v["panel"]                            # how-did-you-hear had 3 real choices
    assert all(n == 1 for n, _ in calls)                             # one question at a time
    assert calls[-1][1] == ["Information Technology", "Acme careers site", "Full-time"]   # sees earlier answers
    assert [(p["choice"], p["kind"]) for p in status.ai_picked] == [
        ("Information Technology", "fact"), ("Acme careers site", "fact"), ("Full-time", "suggestion"),
        ("Daily as part of my workflow", "fact")]
    assert "What type of opportunity are you interested in?*" not in status.needs_you
    assert "Discipline*" not in status.needs_you


def test_your_discipline_setting_wins_over_the_degree_name():
    profile = {**NEW_STYLE_PROFILE, "education": {**NEW_STYLE_PROFILE["education"], "discipline": "Computer Science"}}
    assert apply.answers_from_profile(profile)["discipline"] == "Computer Science"
