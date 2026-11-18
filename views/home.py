"""Public home page ("/"): the firm's front door. Anyone can see it; nothing here reads account or listing data."""
import streamlit as st

from accounts import current_user
from brand import HOME_HEADLINE, masthead

masthead()
st.markdown("<div class='rl-kicker'>Property Investment ROI Analyzer</div>", unsafe_allow_html=True)
st.title(HOME_HEADLINE)
st.markdown(
    "<div class='rl-outcome'>Upload your candidate list and Ridgeline estimates each property's resale value from recent "
    "nearby sales, screens out the weak deals, and ranks the rest by projected return on investment, using Gwinnett "
    "County's public records.</div>", unsafe_allow_html=True)
st.markdown(
    "**Built for** small-scale fix-and-flip investors in Gwinnett County, Georgia, who look at about a hundred listings "
    "to find the one to three worth a walkthrough, and who today check each one by hand against county records and "
    "comparable sales.")

user = current_user()
c1, c2, _ = st.columns([1.1, 1.3, 2])
if user:
    c1.page_link("views/screener.py", label="Open the screener", icon=":material/search:")
    c2.page_link("views/dashboard.py", label="Open your dashboard", icon=":material/dashboard:")
else:
    c1.page_link("views/login.py", label="Log in", icon=":material/login:")
    c2.page_link("views/login.py", label="Create a free account", icon=":material/person_add:")

st.markdown(
    "<div class='rl-tiles'>"
    "<div class='rl-tile'><b>Minutes for the whole list</b><span>Screening one listing by hand took us 15 minutes. "
    "Here you upload the list once and every property comes back ranked, so your time goes to the few worth a visit."
    "</span></div>"
    "<div class='rl-tile'><b>Every number shows its work</b><span>Each property lists the nearby sales behind its resale "
    "value, the renovation band, holding and selling costs, and the arithmetic to its ROI. Estimates are labelled as "
    "estimates.</span></div>"
    "<div class='rl-tile'><b>Know how far to trust it</b><span>Tested on 4,817 past Gwinnett repeat sales: 69.2% of the "
    "resale values it could estimate landed within 15% of the next sale (53.9% counting the ones it could not value)."
    "</span></div>"
    "<div class='rl-tile'><b>Nothing silently dropped</b><span>Every row in your file comes back with a status and a "
    "plain-English reason: ranked, eliminated on price, too few nearby sales, or not found in county records.</span></div>"
    "</div>", unsafe_allow_html=True)

st.markdown("<div class='rl-kicker'>How it works</div>", unsafe_allow_html=True)
st.markdown("1. Upload a CSV of addresses, ZIP codes and asking prices.\n"
            "2. Review the top five by projected ROI, and why the rest were screened out.\n"
            "3. Open any property to see how it was evaluated, then check your saved runs on the dashboard.")
st.caption("A screening tool, not an appraisal, an inspection or professional advice. Renovation figures are "
           "pre-walkthrough estimates: real costs depend on your area, labor and material prices, so use a local "
           "contractor's quote before you buy.")
