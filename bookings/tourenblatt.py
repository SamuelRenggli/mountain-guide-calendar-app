"""Tourenblatt: one PDF with everything the guide needs on the tour day (tour, date, meeting point, participant list
with all planning details, the guide's notes). Built with fpdf2's core fonts, so text is reduced to Latin-1."""

from django.utils import timezone
from django.utils.translation import gettext as _
from fpdf import FPDF

_REPLACE = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "„": '"', "…": "...", "•": "-", "✓": "ja",
            " ": " "}


def _t(value) -> str:
    text = "" if value is None else str(value)
    for a, b in _REPLACE.items():
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def _car(profile) -> str:
    if not profile or profile.car_seats is None:
        return "-"
    return _("voll") if profile.car_seats == 0 else f"+{profile.car_seats}"


def _rows(people) -> list:
    rows = []
    for p in people:
        pr = p.get("profile")
        diet = (pr.get_diet_display() if pr and pr.diet else "") or "-"
        if pr and pr.allergies:
            diet += f"\n{pr.allergies}"
        club = pr.club if pr and pr.club and pr.club != "none" else "-"
        if pr and pr.has_reciprocal_rights:
            club += " (" + _("Gegenrecht") + ")"
        rows.append([
            p["name"] + (" *" if p.get("booker") else ""),
            p.get("phone") or "-",
            (pr.emergency_contact if pr else "") or "-",
            (pr.experience if pr else "") or p.get("experience") or "-",
            diet,
            club,
            (pr.get_travel_card_display() if pr and pr.travel_card else "") or "-",
            _car(pr),
            p.get("note") or "",
        ])
    return rows


def render(*, title: str, dates: str, meeting: str, guide, people: list, notes: str, extra_facts=()) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(12, 12, 12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, _t(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    facts = [(_("Datum"), dates), (_("Treffpunkt"), meeting or "-"), (_("Teilnehmende"), str(len(people))),
             (_("Bergführer"), f"{guide.display_name}, {guide.phone or guide.email}"), *extra_facts]
    for label, value in facts:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(32, 6, _t(label))
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _t(value), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 8.5)
    headings = [_("Name"), _("Telefon"), _("Notfallkontakt"), _("Erfahrung / Können"), _("Ernährung, Allergien"),
                _("Alpenverein"), _("ÖV-Abo"), _("Auto"), _("Notiz")]
    with pdf.table(col_widths=(30, 27, 38, 45, 38, 24, 17, 12, 42), line_height=4.4, padding=1.4) as table:
        head = table.row()
        for h in headings:
            head.cell(_t(h))
        for values in _rows(people):
            row = table.row()
            for v in values:
                row.cell(_t(v))
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 6, _t("* " + _("bucht")), new_x="LMARGIN", new_y="NEXT")

    if notes:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, _t(_("Notizen")), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _t(notes), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(110)
    pdf.cell(0, 5, _t(_("Erstellt am %(day)s mit Tourchef. Vertraulich: enthält Personendaten, nach der Tour vernichten.")
                      % {"day": timezone.localtime().strftime("%d.%m.%Y %H:%M")}))
    return bytes(pdf.output())
