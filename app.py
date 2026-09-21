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
                st.session_state.acting = user["role"] if user["role"] != "presenter" else "officer"
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
# Signed in: sidebar with logo, who you are, your menu
# --------------------------------------------------------------------------

is_presenter = user["role"] == "presenter"
with st.sidebar:
    st.markdown(logo_html(34), unsafe_allow_html=True)
    st.write("")
    if is_presenter:
        roles = [r for r in MENUS]
        acting = st.selectbox("View as", roles, format_func=lambda r: ROLES[r], key="acting")
        acting_user = store.user(acting)          # act exactly as that role's demo user
    else:
        acting = user["role"]
        acting_user = user
    st.markdown(f'**{acting_user["name"]}**<br><span class="tl-muted">{acting_user["org"]}</span><br>'
                f'<span class="tl-role">{ROLES[acting]}</span>', unsafe_allow_html=True)
    st.write("")
    menu = MENUS[acting]
    page = st.radio("Menu", [label for label, _ in menu], key=f"nav_{acting}", label_visibility="collapsed")
    st.write("")
    st.divider()
    if is_presenter:
        if st.button("Reset demo", key="reset", help="Restore the sample data as it was built"):
            restore_snapshot(DATA_DIR)
            _verifier().refresh()
            for k in [k for k in st.session_state if k not in ("user", "acting")]:
                del st.session_state[k]
            st.rerun()
    if st.button("Sign out", key="logout"):
        st.session_state.clear()
        st.rerun()
    st.caption("Demo system · synthetic data")

ctx = Ctx(store=store, registry=_registry(), verifier=_verifier(), user=acting_user, data_dir=DATA_DIR)
dict(menu)[page](ctx)
