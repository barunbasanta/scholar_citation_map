# World Citation Location Map

A GitHub-ready Streamlit application that accepts:

1. A public Google Scholar profile URL
2. A present location in the format `Place, Country`
3. A free OpenAlex API key

It produces:

- An interactive world citation-location map
- Curved, weighted links from the supplied origin place
- Colorful country-based legends
- Place-level and country-level summaries
- High-resolution PNG export
- Vector PDF export
- CSV download of mapped locations

The app deliberately **does not display the researcher's name**. Only geographic places appear in the interface and exported map.

## Method

1. Read a limited set of publication titles from the supplied public Google Scholar profile.
2. Match those titles to OpenAlex works.
3. Retrieve works that cite each matched publication.
4. Read institution IDs from each citing work's authorships.
5. Retrieve the full OpenAlex institution entities to obtain their `geo` coordinates.
6. Aggregate citing papers by place and draw weighted geographic connections.

This means the map is reproducible, but it should not be interpreted as a complete export of Google Scholar citations. Google Scholar and OpenAlex have different coverage, and some works have missing affiliation coordinates.

## OpenAlex API key

OpenAlex currently requires an API key for programmatic API access. Create a free key from your OpenAlex account settings and paste it into the app sidebar. The key is used only for the current Streamlit session.

## Local installation

```bash
git clone <YOUR-REPOSITORY-URL>
cd scholar-citation-map
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Kaleido v1 requires Chrome or Chromium for static PNG/PDF export.

## Docker

```bash
docker build -t scholar-citation-map .
docker run --rm -p 8501:8501 scholar-citation-map
```

Then open `http://localhost:8501`.

## Streamlit Community Cloud

Push these files to GitHub and deploy `app.py`. The included `packages.txt` requests Chromium for Kaleido export. Package availability can vary by Streamlit Cloud image; Docker deployment is the most reproducible option for PNG/PDF export.

## Input example

- Google Scholar URL: `https://scholar.google.com/citations?user=...`
- Present location: `Kharagpur, India`

## Privacy and display behavior

The Google Scholar profile is used only to obtain publication metadata. The app never places the researcher name in the map, table, title, export filename, PNG, or PDF.

## Responsible usage

Google Scholar may rate-limit or block automated access. Keep publication limits moderate and do not repeatedly refresh profiles. This app does not bypass Scholar protections.

## OpenAlex parameter compatibility

The app uses OpenAlex's current snake_case query parameters, including `per_page`.
Older examples using `per-page` return HTTP 400 errors.

## Citation totals

The dashboard displays the total citation count reported on the supplied Google
Scholar profile. That number is separate from the mapped citation-location links.

The map uses OpenAlex citing-work affiliation metadata. Therefore, the number of
mapped links will generally not equal the Google Scholar total: some Scholar
citations are absent from OpenAlex, some citing works lack affiliations or
coordinates, and one citing work can contain multiple institutional locations.
The app never fabricates locations merely to make the totals match.
