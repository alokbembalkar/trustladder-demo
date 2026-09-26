"""
TrustLadder demo: sign in, and see the system as one of six roles.

    ./run_demo.sh                 # local, http://127.0.0.1:8765
    (Render runs the same file; see render.yaml)

Roles and demo logins (one password for all: see trustladder/seed.py):
    hospital · customer · officer · riskhead · registry · auditor · presenter

The presenter account can switch between every role, for showing the whole
story to a panel without logging out five times. "Reset demo" restores the
seeded sample world from a snapshot (fast even on a small server).

Everything is synthetic. This is a demonstration login, not production
security: passwords are stored hashed, but there is no rate limiting or
account management.
"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from trustladder.cases import DATA_DIR
from trustladder.registry import RegistryStore, Verifier
from trustladder.seed import build_and_snapshot, db_path, restore_snapshot
from trustladder.store import ROLES, Store
from ui.pages import MENUS, Ctx
from ui.theme import apply_css, logo_html

st.set_page_config(page_title="TrustLadder", page_icon=str(Path(__file__).parent / "assets" / "logo.svg"),
                   layout="wide")
apply_css()


# --------------------------------------------------------------------------
# The shared world (built once; Render builds it at deploy time)
# --------------------------------------------------------------------------

if not db_path(DATA_DIR).exists():
    with st.spinner("First start: generating the sample hospitals, customers and claims (once)..."):
        build_and_snapshot(DATA_DIR)


@st.cache_resource
def _registry() -> RegistryStore:
    return RegistryStore(DATA_DIR)


@st.cache_resource
def _verifier() -> Verifier:
    return Verifier(_registry())


store = Store(db_path(DATA_DIR))


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def login_screen() -> None:
    _, mid, _ = st.columns([1, 1.25, 1])
    with mid:
        st.write("")
        st.markdown(f'<div style="display:flex;justify-content:center">{logo_html(56)}</div>',
                    unsafe_allow_html=True)
        st.markdown('<p style="text-align:center;color:#64748B;margin-top:6px">From document detection '
                    "to digital trust. Only proof clears; everything else goes to a person.</p>",
                    unsafe_allow_html=True)
        with st.form("login", border=True):
            uid = st.text_input("User ID", key="login_user")
            pw = st.text_input("Password", type="password", key="login_pw")
            ok = st.form_submit_button("Sign in", type="primary", width="stretch")
        if ok:
            user = store.authenticate(uid, pw)
            if user:
                st.session_state.user = user
                st.rerun()
            else:
                st.error("That user ID and password do not match.")
        st.markdown('<p style="text-align:center;color:#94A3B8;font-size:0.8rem">IIM Visakhapatnam · '
                    "Executive Program in Leadership with AI · Capstone Group 7<br>Demonstration system: "
                    "fictional hospitals, people and bills.</p>", unsafe_allow_html=True)


user = st.session_state.get("user")
if not user:
    login_screen()
    st.stop()


# --------------------------------------------------------------------------
# The presenter's guided story: one step per screen, with what to click and say
# --------------------------------------------------------------------------

STEPS = [
    # role, page, short name, what to click, what to say
    ("hospital", "Issue a bill", "Hospital issues",
     "Press **Issue bill and publish**, then press **Download the bill (PDF)**. Keep that file.",
     "The hospital prints a random ticket on the bill and sends the registry two scrambled codes. "
     "No patient data leaves the hospital."),
    ("customer", "Submit a claim", "Customer uploads",
     "Upload the **bill** you just downloaded, then press **Submit claim**.",
     "The customer uploads their bill. The hospital's own record confirms it, so it is paid at once, "
     "with no officer involved."),
    ("customer2", "Submit a claim", "A forged copy",
     "In the sidebar, open **Demo toolkit**, upload the bill and download the **edited copy** "
     "(that is what a forger does on their own computer). Then upload that edited copy here and press "
     "**Submit claim**.",
     "Someone else submits the same bill with a bigger total. Same hospital, same bill number, and the "
     "hospital's record disagrees, so it is held for a person.",),
    ("officer", "Claims inbox", "Officer decides",
     "Open the top claim (the forged copy) and press **Reject as fraud**.",
     "Only claims that could not be proven reach a person, and they arrive with the reason and the evidence."),
    ("officer", "Check any bill", "A fake from nothing",
     "Choose the prepared bill **3. Made from nothing** and press **Check this bill**.",
     "It looks perfect and passes every visual check, yet the hospital never issued it."),
    ("riskhead", "Dashboard", "Risk head",
     "Point at the three numbers.",
     "Fraud stopped, and honest customers wrongly held: both are counted. Only proven bills are paid "
     "automatically."),
    ("registry", "Network", "Registry",
     "Point at 'Patient data held: None'.",
     "The registry holds scrambled codes only. A complete theft would reveal nothing."),
    ("auditor", "Audit trail", "Auditor",
     "Point at the latest rows: the bill, both claims and your decision.",
     "Every machine verdict and every human decision is recorded, for the regulator and for disputes."),
]


def _go(i: int) -> None:
    """Move the guided story to step i and open that step's screen."""
    st.session_state.step = i
    login, page = STEPS[i][0], STEPS[i][1]
    st.session_state[f"nav_{login}"] = page
    st.session_state.pop("cs_last", None)          # each customer sees only their own submission


def forger_toolkit() -> None:
    """Presenter-only: stands in for the PDF editor a forger would use on their own
    computer. It is NOT part of the product, and no role screen can reach it.

    A hospital only ever issues a genuine bill. Someone who wants to defraud the
    insurer downloads that bill and edits it themselves. This little tool does
    exactly that, so the demo can show a forgery of the very bill just issued.
    """
    from trustladder.bills import render_altered
    from trustladder.reader import read_bill
    with st.expander("Demo toolkit: the forger's PDF editor"):
        st.caption("Not part of TrustLadder. It stands in for the editing someone would do on their own "
                   "computer after downloading a genuine bill.")
        up = st.file_uploader("The genuine bill (PDF)", type=["pdf"], key="tool_pdf")
        extra = st.number_input("Raise the total by (Rs)", min_value=1000, max_value=500000, value=50000,
                                step=5000, key="tool_extra")
        if up is not None:
            status, fields, _ = read_bill(up.getvalue())
            if fields is None:
                st.warning("That PDF could not be read, so it cannot be edited here.")
            else:
                edited = render_altered(fields, fields.total_paise + int(extra) * 100,
                                        joined=bool(fields.ticket))
                st.download_button("Download the edited copy", edited,
                                   up.name.replace(".pdf", "") + "_edited.pdf", mime="application/pdf",
                                   key="tool_dl", type="primary")


def guide_bar() -> None:
    i = st.session_state.setdefault("step", 0)
    pills = "".join(
        f'<span class="{"on" if j == i else "done" if j < i else ""}">{j + 1} {name}</span>'
        for j, (_, _, name, _do, _say) in enumerate(STEPS))
    left, right = st.columns([6, 1.3])
    left.markdown(f'<div class="tl-steps">{pills}</div>', unsafe_allow_html=True)
    b1, b2 = right.columns(2)
    b1.button("◂ Back", key="step_back", on_click=_go, args=(max(0, i - 1),), disabled=i == 0)
    b2.button("Next ▸", key="step_next", type="primary", on_click=_go, args=(min(len(STEPS) - 1, i + 1),),
              disabled=i == len(STEPS) - 1)
    _, _, _, do, say = STEPS[i]
    do = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", do)          # **bold** -> <b>bold</b> inside HTML
    st.markdown(f'<div class="tl-guide"><div class="do"><b>Do:</b> {do}</div>'
                f'<div class="say">Say: “{say}”</div></div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Signed in: sidebar with logo, who you are, your menu
# --------------------------------------------------------------------------

is_presenter = user["role"] == "presenter"
if is_presenter and "step" not in st.session_state:
    _go(0)
acting_login = STEPS[st.session_state["step"]][0] if is_presenter else user["user_id"]
acting_user = store.user(acting_login) if is_presenter else user   # act as that demo login
acting = acting_user["role"]

with st.sidebar:
    st.markdown(logo_html(34), unsafe_allow_html=True)
    st.write("")
    st.markdown(f'**{acting_user["name"]}**<br><span class="tl-muted">{acting_user["org"]}</span><br>'
                f'<span class="tl-role">{ROLES[acting]}</span>', unsafe_allow_html=True)
    st.write("")
    menu = MENUS[acting]
    if len(menu) > 1:
        page = st.radio("Menu", [label for label, _ in menu], key=f"nav_{acting_login}",
                        label_visibility="collapsed")
    else:
        page = menu[0][0]
    st.divider()
    st.toggle("Show technical details", key="details",
              help="Findings, rule numbers, the evidence graph and the audit record")
    if is_presenter:
        forger_toolkit()
        if st.button("Reset demo", key="reset", help="Restore the sample data as it was built"):
            restore_snapshot(DATA_DIR)
            _verifier().refresh()
            for k in [k for k in st.session_state if k not in ("user",)]:
                del st.session_state[k]
            _go(0)
            st.rerun()
    if st.button("Sign out", key="logout"):
        st.session_state.clear()
        st.rerun()
    st.caption("Demo system · synthetic data")

if is_presenter:
    guide_bar()
ctx = Ctx(store=store, registry=_registry(), verifier=_verifier(), user=acting_user, data_dir=DATA_DIR)
dict(menu)[page](ctx)
