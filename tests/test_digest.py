import unittest
from datetime import date

from digest.filtering import FollowedAuthor, assign_themes, load_themes
from digest.sources import Author, Paper
from digest.text import clean_markup, clean_tex_text, tex_to_html

THEMES = load_themes([
    {"name": "Arctic sea ice", "priority": 6, "keywords": ["sea ice", "Beaufort Gyre"]},
    {"name": "Convection", "priority": 7, "keywords": ["convect*", "Rayleigh-Benard"]},
    {"name": "Rotating convection", "priority": 1,
     "keywords": [r"re:\brotat\w*\b.{0,60}\bconvect|\bconvect\w*\b.{0,60}\brotat"]},
    {"name": "Turbulence", "priority": 8, "keywords": ["turbulen*"]},
])


def paper(title, abstract="", authors=(), watch=True):
    return Paper(key="doi:x", title_html=title, authors=list(authors), venue="J", created=date.today(),
                 published=None, url="", abstract_html=[abstract] if abstract else [], in_watchlist=watch)


class Text(unittest.TestCase):
    def test_jats(self):
        raw = ("<jats:title>Abstract</jats:title><jats:p>Speed of 1.8 m s\n <jats:sup>−1</jats:sup>\n , "
               "at <jats:inline-formula><jats:alternatives><mml:math><mml:mi>x</mml:mi></mml:math>"
               "<jats:tex-math>$\\textit{Re} = 300$</jats:tex-math></jats:alternatives></jats:inline-formula> "
               "in <jats:italic>situ</jats:italic>.</jats:p>"
               "<jats:sec><jats:title>Plain Language Summary</jats:title><jats:p>drop me</jats:p></jats:sec>")
        self.assertEqual(clean_markup(raw), ["Speed of 1.8 m s<sup>−1</sup>, at <i>Re</i> = 300 in <i>situ</i>."])

    def test_tex(self):
        self.assertEqual(tex_to_html(r"Ra \sim 10^{9}"), "Ra ∼ 10<sup>9</sup>")
        self.assertEqual(tex_to_html(r"\frac{N}{f}"), "N/f")
        self.assertEqual(clean_tex_text("We vary $Ro<1$ & more"), ["We vary Ro&lt;1 &amp; more"])


class Themes(unittest.TestCase):
    def test_priority_and_tags(self):
        p = paper("Rotating Rayleigh–Bénard convection beneath sea-ice", "turbulent plumes")
        assign_themes(p, THEMES)
        self.assertEqual(p.theme, "Rotating convection")
        self.assertEqual(p.other_themes, ["Arctic sea ice", "Convection"])

    def test_no_partial_words(self):
        p = paper("Seasonal ice-free summers")            # "sea ice" must not match "seasonal ice"
        assign_themes(p, THEMES)
        self.assertIsNone(p.theme)


class Authors(unittest.TestCase):
    ml = FollowedAuthor.from_cfg({"name": "Mary-Louise Timmermans", "orcid": "0000-0003-2718-2556"})
    sam = FollowedAuthor.from_cfg({"name": "Sam De Abreu", "given": ["Sam", "Samuel"], "family": "De Abreu"})
    er = FollowedAuthor.from_cfg({"name": "Erica Rosenblum", "orcid": "0000-0002-9330-2010",
                                  "exclude": ["Erica Bree Rosenblum"]})

    def test_name_variants(self):
        p = paper("t")
        for given in ["Mary‐Louise", "M.-L.", "M.L.", "Mary-Louise"]:
            self.assertTrue(self.ml.matches(Author(given, "Timmermans"), p), given)
        self.assertFalse(self.ml.matches(Author("Mary Louise", "Fellows"), p))

    def test_orcid_wins(self):
        p = paper("t", watch=False)
        self.assertTrue(self.ml.matches(Author("M.", "Timmermans", "0000-0003-2718-2556"), p))
        self.assertFalse(self.ml.matches(Author("Mary-Louise", "Timmermans", "0000-0000-0000-0001"), p))
        self.assertFalse(self.ml.matches(Author("Mary-Louise", "Timmermans"), p))  # off-list, no ORCID

    def test_family_with_particle(self):
        p = paper("t")
        self.assertTrue(self.sam.matches(Author("Sam", "De Abreu"), p))
        self.assertTrue(self.sam.matches(Author("Sam De", "Abreu"), p))          # arXiv-style split
        self.assertFalse(self.sam.matches(Author("Matheus", "de Abreu"), p))
        self.assertFalse(self.sam.matches(Author("S.", "De Abreu"), paper("t", watch=False)))

    def test_tentative_off_list(self):
        from digest.filtering import tag_followed
        p = paper("t", authors=[Author("Mary-Louise", "Timmermans")], watch=False)
        tag_followed(p, [self.ml])
        self.assertEqual((p.followed, p.followed_tentative), ([], ["Mary-Louise Timmermans"]))
        p = paper("t", authors=[Author("M.", "Timmermans")], watch=False)
        tag_followed(p, [self.ml])
        self.assertEqual((p.followed, p.followed_tentative), ([], []))

    def test_exclude(self):
        self.assertFalse(self.er.matches(Author("Erica Bree", "Rosenblum"), paper("t")))
        self.assertTrue(self.er.matches(Author("Erica", "Rosenblum"), paper("t")))


if __name__ == "__main__":
    unittest.main()
