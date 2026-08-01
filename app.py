from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from geopy.geocoders import Nominatim
from scholarly import scholarly


st.set_page_config(
    page_title="World Citation Location Map",
    page_icon="🌍",
    layout="wide",
)

OPENALEX_BASE = "https://api.openalex.org"
PALETTE = [
    "#ef476f", "#f78c6b", "#ffd166", "#06d6a0", "#118ab2",
    "#7b61ff", "#b5179e", "#43aa8b", "#f8961e", "#577590",
    "#e63946", "#2a9d8f", "#4361ee", "#8338ec", "#ff006e",
]

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
    div[data-testid="stMetric"] {
        border: 1px solid rgba(128,128,128,.22);
        border-radius: 14px;
        padding: 10px 14px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def scholar_user_id(profile_url: str) -> str:
    """Extract the public Google Scholar user ID from a profile URL."""
    parsed = urlparse(profile_url.strip())
    user = parse_qs(parsed.query).get("user", [""])[0].strip()
    if not user:
        match = re.search(r"[?&]user=([^&]+)", profile_url)
        user = match.group(1) if match else ""
    if not user:
        raise ValueError("The URL does not contain a Google Scholar user ID.")
    return user


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 30)
def geocode_place(place_country: str) -> dict:
    geolocator = Nominatim(user_agent="world-citation-location-map/1.0")
    location = geolocator.geocode(place_country, exactly_one=True, timeout=15)
    if location is None:
        raise ValueError(
            f'Could not geocode "{place_country}". Use the format "Place, Country".'
        )
    return {
        "label": place_country.strip(),
        "lat": float(location.latitude),
        "lon": float(location.longitude),
    }


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def scholar_publications(profile_url: str, publication_limit: int) -> tuple[list[dict], dict]:
    """
    Read only publication metadata from Google Scholar.

    Google Scholar may rate-limit automated requests. The app intentionally
    retrieves a limited number of publications and falls back with a clear error.
    """
    user_id = scholar_user_id(profile_url)
    author = scholarly.search_author_id(user_id)
    author = scholarly.fill(
        author,
        sections=["publications", "citations"],
        sortby="citedby",
        publication_limit=publication_limit,
    )
    publications = []
    for pub in author.get("publications", [])[:publication_limit]:
        bib = pub.get("bib", {})
        title = (bib.get("title") or "").strip()
        if not title:
            continue
        publications.append(
            {
                "title": title,
                "year": bib.get("pub_year"),
                "scholar_citations": int(pub.get("num_citations") or 0),
            }
        )
    if not publications:
        raise RuntimeError("No publications were returned from the Scholar profile.")

    metrics = {
        "total_citations": int(author.get("citedby") or 0),
        "citations_5y": int(author.get("citedby5y") or 0),
        "hindex": int(author.get("hindex") or 0),
        "i10index": int(author.get("i10index") or 0),
    }
    return publications, metrics


def oa_headers(api_key: str) -> dict:
    headers = {"User-Agent": "WorldCitationLocationMap/1.0"}
    if api_key.strip():
        headers["api_key"] = api_key.strip()
    return headers


def oa_get(path_or_url: str, params: dict, api_key: str) -> dict:
    url = path_or_url if path_or_url.startswith("http") else f"{OPENALEX_BASE}{path_or_url}"
    params = dict(params)
    if api_key.strip():
        params["api_key"] = api_key.strip()
    response = requests.get(url, params=params, headers=oa_headers(api_key), timeout=35)
    response.raise_for_status()
    return response.json()


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def title_similarity(a: str, b: str) -> float:
    aa, bb = set(normalize_title(a).split()), set(normalize_title(b).split())
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 7)
def match_openalex_work(title: str, api_key: str) -> dict | None:
    data = oa_get(
        "/works",
        {"search": title, "per_page": 5, "select": "id,display_name,cited_by_count"},
        api_key,
    )
    candidates = data.get("results", [])
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda item: title_similarity(title, item.get("display_name", "")),
        reverse=True,
    )
    best = ranked[0]
    if title_similarity(title, best.get("display_name", "")) < 0.45:
        return None
    return best


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 30)
def fetch_openalex_institution(institution_id: str, api_key: str) -> dict | None:
    """
    Authorship records generally contain compact institution objects without geo.
    Retrieve the full institution entity, which contains the geo object.
    """
    if not institution_id:
        return None
    short_id = institution_id.rstrip("/").rsplit("/", 1)[-1]
    try:
        return oa_get(
            f"/institutions/{short_id}",
            {"select": "id,display_name,country_code,geo"},
            api_key,
        )
    except requests.RequestException:
        return None


def institution_location(inst: dict) -> dict | None:
    geo = inst.get("geo") or {}
    lat, lon = geo.get("latitude"), geo.get("longitude")
    if lat is None or lon is None:
        return None

    city = (geo.get("city") or "").strip()
    country = (
        geo.get("country")
        or geo.get("country_code")
        or inst.get("country_code")
        or "Unknown"
    )
    display_name = (inst.get("display_name") or "").strip()
    place = city or display_name or str(country)
    label = place
    if country and str(country).lower() not in place.lower():
        label = f"{place}, {country}"

    return {
        "institution": display_name,
        "place": label,
        "country": str(country),
        "lat": float(lat),
        "lon": float(lon),
    }


def extract_locations_from_citing_work(work: dict, api_key: str) -> list[dict]:
    found = {}
    for authorship in work.get("authorships", []):
        for compact_inst in authorship.get("institutions", []):
            institution_id = compact_inst.get("id") or ""
            full_inst = fetch_openalex_institution(institution_id, api_key)
            if not full_inst:
                continue
            loc = institution_location(full_inst)
            if not loc:
                continue
            key = (
                round(loc["lat"], 4),
                round(loc["lon"], 4),
                loc["institution"].lower(),
            )
            found[key] = loc
    return list(found.values())


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24 * 7)
def citing_works_for_openalex_id(
    openalex_id: str,
    per_work_limit: int,
    api_key: str,
) -> list[dict]:
    work_id = openalex_id.rsplit("/", 1)[-1]
    data = oa_get(
        "/works",
        {
            "filter": f"cites:{work_id}",
            "per_page": min(per_work_limit, 100),
            "select": "id,display_name,publication_year,authorships",
        },
        api_key,
    )
    return data.get("results", [])


def aggregate_citation_locations(
    publications: list[dict],
    per_work_limit: int,
    api_key: str,
    progress,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    location_counts = defaultdict(lambda: {
        "count": 0,
        "papers": set(),
        "institutions": set(),
        "country": "",
        "lat": 0.0,
        "lon": 0.0,
        "place": "",
    })
    match_rows = []
    total = max(len(publications), 1)

    for index, publication in enumerate(publications, start=1):
        title = publication["title"]
        progress.progress(
            index / total,
            text=f"Matching publication {index} of {total}",
        )
        match = match_openalex_work(title, api_key)
        if not match:
            match_rows.append({
                "Publication": title,
                "OpenAlex match": "Not found",
                "OpenAlex citations": 0,
            })
            continue

        match_rows.append({
            "Publication": title,
            "OpenAlex match": match.get("display_name", ""),
            "OpenAlex citations": int(match.get("cited_by_count") or 0),
        })

        citing_works = citing_works_for_openalex_id(
            match["id"], per_work_limit, api_key
        )
        match_rows[-1]["Retrieved citing works"] = len(citing_works)
        match_rows[-1]["Citing works with institution IDs"] = sum(
            1
            for work in citing_works
            if any(
                inst.get("id")
                for authorship in work.get("authorships", [])
                for inst in authorship.get("institutions", [])
            )
        )
        for citing_work in citing_works:
            locations = extract_locations_from_citing_work(citing_work, api_key)
            # Each institution location is counted once per citing work.
            for loc in locations:
                key = (
                    round(loc["lat"], 3),
                    round(loc["lon"], 3),
                    loc["place"].lower(),
                )
                row = location_counts[key]
                row["count"] += 1
                row["papers"].add(citing_work.get("display_name", "Untitled work"))
                if loc["institution"]:
                    row["institutions"].add(loc["institution"])
                row["country"] = loc["country"]
                row["lat"] = loc["lat"]
                row["lon"] = loc["lon"]
                row["place"] = loc["place"]

    rows = []
    for value in location_counts.values():
        rows.append({
            "Place": value["place"],
            "Country": value["country"],
            "Latitude": value["lat"],
            "Longitude": value["lon"],
            "Citing papers": value["count"],
            "Institutions": "; ".join(sorted(value["institutions"])),
            "Example papers": "; ".join(sorted(value["papers"])[:5]),
        })

    locations_df = pd.DataFrame(rows)
    if not locations_df.empty:
        locations_df = locations_df.sort_values(
            ["Citing papers", "Country", "Place"],
            ascending=[False, True, True],
        ).reset_index(drop=True)

    return locations_df, pd.DataFrame(match_rows)


def bezier_arc(lon1: float, lat1: float, lon2: float, lat2: float, points: int = 35):
    """Create a visually smooth curved line in lon/lat space."""
    # Keep longitudes on the shorter visual side of the map where possible.
    delta_lon = lon2 - lon1
    if delta_lon > 180:
        lon2 -= 360
    elif delta_lon < -180:
        lon2 += 360

    mid_lon = (lon1 + lon2) / 2
    distance = math.hypot(lon2 - lon1, lat2 - lat1)
    mid_lat = min(82, (lat1 + lat2) / 2 + min(28, distance * 0.15))

    lons, lats = [], []
    for i in range(points):
        t = i / (points - 1)
        lon = (1 - t) ** 2 * lon1 + 2 * (1 - t) * t * mid_lon + t ** 2 * lon2
        lat = (1 - t) ** 2 * lat1 + 2 * (1 - t) * t * mid_lat + t ** 2 * lat2
        # Wrap for Plotly.
        if lon > 180:
            lon -= 360
        if lon < -180:
            lon += 360
        lons.append(lon)
        lats.append(lat)
    return lons, lats


def build_map(
    origin: dict,
    locations_df: pd.DataFrame,
    minimum_count: int,
    show_labels: bool,
    projection: str,
) -> go.Figure:
    filtered = locations_df[locations_df["Citing papers"] >= minimum_count].copy()
    fig = go.Figure()

    if filtered.empty:
        fig.add_annotation(
            text="No locations meet the selected minimum.",
            x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font={"size": 22},
        )
    else:
        max_count = max(int(filtered["Citing papers"].max()), 1)
        country_order = list(dict.fromkeys(filtered["Country"].fillna("Unknown")))
        country_colors = {
            country: PALETTE[i % len(PALETTE)]
            for i, country in enumerate(country_order)
        }

        # Curved connection lines, grouped by country for a colorful legend.
        for country in country_order:
            country_df = filtered[filtered["Country"] == country]
            first = True
            for _, row in country_df.iterrows():
                lons, lats = bezier_arc(
                    origin["lon"], origin["lat"],
                    float(row["Longitude"]), float(row["Latitude"])
                )
                width = 1.0 + 5.0 * math.sqrt(float(row["Citing papers"]) / max_count)
                fig.add_trace(go.Scattergeo(
                    lon=lons,
                    lat=lats,
                    mode="lines",
                    line={"width": width, "color": country_colors[country]},
                    opacity=0.62,
                    name=str(country),
                    legendgroup=str(country),
                    showlegend=first,
                    hoverinfo="skip",
                ))
                first = False

        # Endpoints by country.
        for country in country_order:
            group = filtered[filtered["Country"] == country]
            sizes = 9 + 24 * (group["Citing papers"] / max_count).pow(0.5)
            hover = (
                "<b>" + group["Place"].astype(str) + "</b><br>"
                + "Citing papers: " + group["Citing papers"].astype(str)
                + "<br>Institutions: " + group["Institutions"].replace("", "Not available")
            )
            fig.add_trace(go.Scattergeo(
                lon=group["Longitude"],
                lat=group["Latitude"],
                mode="markers+text" if show_labels else "markers",
                text=group["Place"] if show_labels else None,
                textposition="top center",
                textfont={"size": 10, "color": "#172033"},
                marker={
                    "size": sizes,
                    "color": country_colors[country],
                    "line": {"width": 1.4, "color": "white"},
                    "opacity": 0.92,
                },
                hovertext=hover,
                hovertemplate="%{hovertext}<extra></extra>",
                name=f"{country} locations",
                legendgroup=str(country),
                showlegend=False,
            ))

    # Origin is always displayed without a person name.
    fig.add_trace(go.Scattergeo(
        lon=[origin["lon"]],
        lat=[origin["lat"]],
        mode="markers+text",
        text=[origin["label"]],
        textposition="bottom center",
        textfont={"size": 12, "color": "#111827"},
        marker={
            "size": 18,
            "color": "#111827",
            "symbol": "star",
            "line": {"width": 2.2, "color": "#ffd166"},
        },
        hovertemplate="<b>%{text}</b><br>Origin location<extra></extra>",
        name="Origin location",
        legendrank=1,
    ))

    fig.update_geos(
        projection_type=projection,
        showland=True,
        landcolor="#f4f7fb",
        showocean=True,
        oceancolor="#dff3ff",
        showlakes=True,
        lakecolor="#dff3ff",
        showcountries=True,
        countrycolor="#aab7c4",
        countrywidth=0.7,
        showcoastlines=True,
        coastlinecolor="#8091a5",
        coastlinewidth=0.8,
        bgcolor="white",
    )
    fig.update_layout(
        title={
            "text": f"World Citation Location Map<br><sup>Connections originate from {origin['label']}</sup>",
            "x": 0.5,
            "xanchor": "center",
            "font": {"size": 24, "color": "#172033"},
        },
        paper_bgcolor="white",
        plot_bgcolor="white",
        height=720,
        margin={"l": 8, "r": 8, "t": 86, "b": 18},
        legend={
            "title": {"text": "Citing-location countries"},
            "orientation": "v",
            "x": 0.01,
            "y": 0.02,
            "xanchor": "left",
            "yanchor": "bottom",
            "bgcolor": "rgba(255,255,255,0.88)",
            "bordercolor": "#b8c4d1",
            "borderwidth": 1,
            "font": {"size": 11},
        },
        font={"family": "Arial, sans-serif"},
    )
    return fig


def export_figure(fig: go.Figure) -> tuple[bytes | None, bytes | None, str | None]:
    try:
        png = fig.to_image(
            format="png",
            width=4800,
            height=2700,
            scale=1.5,
        )
        pdf = fig.to_image(
            format="pdf",
            width=1600,
            height=900,
            scale=1,
        )
        return png, pdf, None
    except Exception as exc:
        return None, None, str(exc)


st.title("🌍 World Citation Location Map")
st.caption(
    "Create an interactive citation-location map from a public Google Scholar "
    "profile. The visualization displays places only—researcher names are never shown."
)

with st.sidebar:
    st.header("Map input")
    profile_url = st.text_input(
        "Google Scholar profile URL",
        placeholder="https://scholar.google.com/citations?user=...",
    )
    origin_text = st.text_input(
        "Present location",
        placeholder="Place, Country",
        help='Use a clear format such as "Kharagpur, India" or "Paris, France".',
    )
    openalex_key = st.text_input(
        "OpenAlex API key",
        type="password",
        help="OpenAlex currently requires a free API key for API access.",
    )

    st.header("Collection limits")
    publication_limit = st.slider(
        "Top Scholar publications",
        min_value=3,
        max_value=50,
        value=15,
        step=1,
        help="Higher values increase coverage and processing time.",
    )
    per_work_limit = st.slider(
        "Citing papers per publication",
        min_value=10,
        max_value=100,
        value=75,
        step=5,
    )

    generate = st.button(
        "Generate citation map",
        type="primary",
        use_container_width=True,
    )

if generate:
    if not profile_url.strip() or not origin_text.strip() or not openalex_key.strip():
        st.error(
            "Enter a Google Scholar profile URL, a location in the format "
            '"Place, Country", and an OpenAlex API key.'
        )
    else:
        try:
            origin = geocode_place(origin_text)
            with st.spinner("Reading publication metadata from Google Scholar…"):
                publications, scholar_metrics = scholar_publications(
                    profile_url, publication_limit
                )

            progress = st.progress(0, text="Preparing citation search…")
            locations_df, matches_df = aggregate_citation_locations(
                publications,
                per_work_limit,
                openalex_key,
                progress,
            )
            progress.empty()

            st.session_state["origin"] = origin
            st.session_state["locations_df"] = locations_df
            st.session_state["matches_df"] = matches_df
            st.session_state["publication_count"] = len(publications)
            st.session_state["scholar_metrics"] = scholar_metrics
        except requests.HTTPError as exc:
            st.error(f"An external data service returned an error: {exc}")
        except Exception as exc:
            st.error(str(exc))
            st.info(
                "Google Scholar may temporarily block automated requests. "
                "Retry later, reduce the publication limit, or run the app from a local machine."
            )

if "locations_df" in st.session_state:
    locations_df = st.session_state["locations_df"]
    origin = st.session_state["origin"]
    matches_df = st.session_state["matches_df"]

    if locations_df.empty:
        st.warning(
            "No citing-work affiliations with geographic coordinates were found. "
            "Check the OpenAlex matching audit. Some citing works may not report institution IDs or institution coordinates."
        )
    else:
        scholar_metrics = st.session_state.get("scholar_metrics", {})
        total_scholar_citations = int(scholar_metrics.get("total_citations") or 0)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Origin", origin["label"])
        m2.metric("Google Scholar citations", f"{total_scholar_citations:,}")
        m3.metric("Mapped places", f"{len(locations_df):,}")
        m4.metric("Countries", f"{locations_df['Country'].nunique():,}")
        m5.metric(
            "Mapped citation-location links",
            f"{int(locations_df['Citing papers'].sum()):,}",
            help=(
                "This is not the Google Scholar citation total. A citing paper can "
                "contribute more than one location when it has multiple affiliations."
            ),
        )

        st.info(
            "The Google Scholar citation metric is read from the profile and should "
            "match the total displayed there at retrieval time. The geographic map "
            "uses OpenAlex affiliation data, so its mapped total will usually be "
            "lower—and can never be forced to equal Scholar without inventing locations."
        )

        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            minimum_count = st.slider(
                "Minimum citing papers per place",
                min_value=1,
                max_value=max(1, int(locations_df["Citing papers"].max())),
                value=1,
            )
        with c2:
            projection = st.selectbox(
                "Projection",
                ["natural earth", "robinson", "orthographic", "equirectangular"],
                index=0,
            )
        with c3:
            show_labels = st.checkbox("Show place labels", value=True)

        fig = build_map(
            origin,
            locations_df,
            minimum_count,
            show_labels,
            projection,
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
            config={
                "displaylogo": False,
                "scrollZoom": True,
                "toImageButtonOptions": {
                    "format": "png",
                    "filename": "world_citation_location_map",
                    "height": 1800,
                    "width": 3200,
                    "scale": 2,
                },
            },
        )

        st.subheader("Downloads")
        with st.spinner("Preparing high-resolution exports…"):
            png_bytes, pdf_bytes, export_error = export_figure(fig)

        d1, d2, d3 = st.columns(3)
        with d1:
            if png_bytes:
                st.download_button(
                    "Download high-resolution PNG",
                    data=png_bytes,
                    file_name="world_citation_location_map.png",
                    mime="image/png",
                    use_container_width=True,
                )
            else:
                st.button(
                    "PNG export unavailable",
                    disabled=True,
                    use_container_width=True,
                )
        with d2:
            if pdf_bytes:
                st.download_button(
                    "Download vector PDF",
                    data=pdf_bytes,
                    file_name="world_citation_location_map.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            else:
                st.button(
                    "PDF export unavailable",
                    disabled=True,
                    use_container_width=True,
                )
        with d3:
            csv_bytes = locations_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download location data",
                data=csv_bytes,
                file_name="citation_locations.csv",
                mime="text/csv",
                use_container_width=True,
            )

        if export_error:
            st.warning(
                "Static export requires Kaleido and Chrome/Chromium. "
                f"Interactive mapping still works. Technical detail: {export_error}"
            )

        left, right = st.columns(2)
        with left:
            st.subheader("Top mapped places")
            st.dataframe(
                locations_df[
                    ["Place", "Country", "Citing papers", "Institutions"]
                ].head(50),
                use_container_width=True,
                hide_index=True,
            )
        with right:
            country_summary = (
                locations_df.groupby("Country", as_index=False)["Citing papers"]
                .sum()
                .sort_values("Citing papers", ascending=False)
            )
            st.subheader("Country summary")
            st.dataframe(
                country_summary,
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("Citation and OpenAlex matching audit"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(
                "Scholar citations",
                f"{int(scholar_metrics.get('total_citations') or 0):,}",
            )
            c2.metric(
                "Scholar citations (5 years)",
                f"{int(scholar_metrics.get('citations_5y') or 0):,}",
            )
            c3.metric("Scholar h-index", int(scholar_metrics.get("hindex") or 0))
            c4.metric("Scholar i10-index", int(scholar_metrics.get("i10index") or 0))
            st.dataframe(matches_df, use_container_width=True, hide_index=True)

st.divider()
st.caption(
    "Method: Scholar publication titles are matched to OpenAlex works; citing works "
    "are then aggregated by geocoded institutional affiliations. Google Scholar and "
    "OpenAlex coverage can differ, so the result is a reproducible location map—not "
    "a claim of complete Scholar citation coverage."
)
