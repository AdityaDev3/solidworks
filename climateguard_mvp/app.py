import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.express as px
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import average_precision_score, recall_score, precision_score, brier_score_loss
from sklearn.dummy import DummyClassifier

st.set_page_config(page_title="ClimateGuard", page_icon="🌦️", layout="wide")

st.title("🌦️ ClimateGuard")
st.caption("Climate-informed dengue early-warning prototype | Research and decision-support only")

st.warning(
    "This prototype is not a validated public-health warning system. "
    "Predictions are for demonstration/research, not diagnosis or official outbreak declarations."
)

with st.sidebar:
    st.header("Data")
    mode = st.radio("Choose input", ["Upload historical CSV", "Explore demo data"])
    st.markdown("**CSV columns required:** `date`, `cases`, `rainfall_mm`, `temperature_c`")
    st.markdown("Optional: `humidity_pct`, `population`, `location`.")
    st.caption("Use real, documented data for any claims about predictive performance.")

def make_demo():
    # Explicitly synthetic: never present these values as real surveillance observations.
    rng = np.random.default_rng(21)
    dates = pd.date_range("2018-01-07", periods=260, freq="W-SUN")
    season = 1.0 + 0.75 * np.sin(2 * np.pi * (dates.dayofyear.to_numpy() / 365.25 - 0.35))
    rain = np.maximum(0, rng.gamma(2.0, 22.0, len(dates)) * season)
    temp = 27 + 3 * np.sin(2 * np.pi * (dates.dayofyear.to_numpy() / 365.25 - 0.1)) + rng.normal(0, 0.8, len(dates))
    humidity = np.clip(60 + rain * 0.22 + rng.normal(0, 7, len(dates)), 25, 98)
    cases = np.maximum(0, rng.poisson(np.maximum(1, 5 + 0.07 * rain + 1.5 * np.maximum(temp - 27, 0) + 4 * season)))
    return pd.DataFrame({"date": dates, "cases": cases, "rainfall_mm": rain,
                         "temperature_c": temp, "humidity_pct": humidity,
                         "population": 500000, "location": "Synthetic Demo Location"})

def normalize(df):
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    aliases = {
        "calendar_start_date": "date", "dengue_total": "cases",
        "rainfall": "rainfall_mm", "precipitation": "rainfall_mm",
        "temperature": "temperature_c", "temp": "temperature_c",
        "humidity": "humidity_pct"
    }
    df = df.rename(columns={k:v for k,v in aliases.items() if k in df.columns and v not in df.columns})
    needed = ["date", "cases", "rainfall_mm", "temperature_c"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["cases", "rainfall_mm", "temperature_c", "humidity_pct", "population"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "cases"]).sort_values("date")
    # The MVP expects weekly observations. Do not invent weekly cases from monthly data.
    df = df.drop_duplicates(subset=["date"], keep="last")
    return df.reset_index(drop=True)

def add_features(df):
    d = df.copy().sort_values("date").reset_index(drop=True)
    # Assumes rows are evenly spaced weekly. Check cadence before interpreting results.
    for col in ["cases", "rainfall_mm", "temperature_c", "humidity_pct"]:
        if col in d:
            for lag in [1, 2, 3, 4]:
                d[f"{col}_lag{lag}"] = d[col].shift(lag)
    d["month"] = d["date"].dt.month
    d["target_next_week"] = d["cases"].shift(-1)
    return d

def get_weather(lat, lon, start, end):
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        "parameters": "PRECTOTCORR,T2M,RH2M",
        "community": "AG",
        "longitude": lon, "latitude": lat,
        "start": pd.Timestamp(start).strftime("%Y%m%d"),
        "end": pd.Timestamp(end).strftime("%Y%m%d"),
        "format": "JSON", "time-standard": "UTC"
    }
    response = requests.get(url, params=params, timeout=45)
    response.raise_for_status()
    payload = response.json()
    values = payload["properties"]["parameter"]
    out = pd.DataFrame({
        "date": pd.to_datetime(list(values["T2M"].keys()), format="%Y%m%d"),
        "rainfall_mm": list(values["PRECTOTCORR"].values()),
        "temperature_c": list(values["T2M"].values()),
        "humidity_pct": list(values["RH2M"].values())
    })
    # Aggregate daily weather to weeks ending Sunday.
    out["date"] = out["date"].dt.to_period("W-SUN").dt.end_time.dt.normalize()
    return out.groupby("date", as_index=False).agg(
        rainfall_mm=("rainfall_mm", "sum"),
        temperature_c=("temperature_c", "mean"),
        humidity_pct=("humidity_pct", "mean")
    )

if mode == "Upload historical CSV":
    uploaded = st.file_uploader("Upload a cleaned, location-specific weekly CSV", type=["csv"])
    if uploaded:
        try:
            df = normalize(pd.read_csv(uploaded))
            is_demo = False
        except Exception as e:
            st.error(str(e))
            st.stop()
    else:
        st.info("Upload a CSV to train on real data, or switch to Explore demo data to inspect the interface.")
        st.stop()
else:
    df = make_demo()
    is_demo = True
    st.info("DEMO MODE: all displayed records are synthetic and do not describe a real place or outbreak.")

if "location" not in df.columns:
    df["location"] = "Uploaded location"
location_names = sorted(df["location"].dropna().astype(str).unique().tolist())
selected_location = st.sidebar.selectbox("Location", location_names)
df = df[df["location"].astype(str) == selected_location].copy().sort_values("date")

if len(df) < 40:
    st.error(f"Only {len(df)} observations found. Aim for at least 40 weekly records; more is better.")
    st.stop()

if "rainfall_mm" not in df or "temperature_c" not in df:
    st.error("Weather columns are required. Join weather to the same location and weekly dates before training.")
    st.stop()

# Cadence guard: weekly data should have about 7 days between observations.
gaps = df["date"].diff().dropna().dt.days
weekly_share = float((gaps.between(5, 9)).mean()) if len(gaps) else 0
if weekly_share < 0.8:
    st.error(
        f"Data does not look consistently weekly ({weekly_share:.0%} of gaps are 5–9 days). "
        "Do not upsample monthly or irregular case data into invented weekly observations. "
        "Prepare a consistent weekly or monthly dataset first."
    )
    st.stop()

st.subheader("Data overview")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Observations", f"{len(df):,}")
c2.metric("First date", df["date"].min().date().isoformat())
c3.metric("Last date", df["date"].max().date().isoformat())
c4.metric("Missing required values", f"{int(df[['cases','rainfall_mm','temperature_c']].isna().sum().sum())}")

if is_demo:
    st.caption("Synthetic data only — use this mode to test the UI, not to report model accuracy.")
st.plotly_chart(px.line(df, x="date", y="cases", title="Reported dengue cases per observation period"), use_container_width=True)
weather_long = df.melt(id_vars="date", value_vars=["rainfall_mm", "temperature_c"], var_name="variable", value_name="value")
st.plotly_chart(px.line(weather_long, x="date", y="value", color="variable", title="Weather indicators (different units; interpret separately)"), use_container_width=True)

# Build a next-week elevated-activity target based only on the training period threshold.
feat = add_features(df)
features = [c for c in feat.columns if any(c.startswith(x + "_lag") for x in ["cases", "rainfall_mm", "temperature_c", "humidity_pct"])]
features += ["month"]
if not features:
    st.error("Could not build model features.")
    st.stop()

# Threshold is calculated on the training portion only to reduce leakage.
split = int(len(feat) * 0.8)
train_raw = feat.iloc[:split].copy()
threshold = float(train_raw["target_next_week"].dropna().quantile(0.75))
feat["target"] = (feat["target_next_week"] > threshold).astype("float")
model_data = feat.dropna(subset=features + ["target_next_week"]).copy()
model_data["target"] = (model_data["target_next_week"] > threshold).astype(int)
split_idx = int(len(model_data) * 0.8)
train = model_data.iloc[:split_idx]
test = model_data.iloc[split_idx:]

st.subheader("Forecast model")
st.write(f"Target: elevated case activity next week, defined as more than the training-period 75th percentile ({threshold:.1f} cases). This is a prototype research label, not an official outbreak threshold.")
if train["target"].nunique() < 2 or len(test) < 5:
    st.error("Insufficient target variation or test data. Use a longer, consistent time series.")
    st.stop()

model = RandomForestClassifier(n_estimators=250, max_depth=5, class_weight="balanced", random_state=42)
model.fit(train[features], train["target"])
test_prob = model.predict_proba(test[features])[:, 1]
test_pred = (test_prob >= 0.5).astype(int)

m1, m2, m3 = st.columns(3)
m1.metric("Test observations", len(test))
m2.metric("Recall at 0.5 threshold", f"{recall_score(test['target'], test_pred, zero_division=0):.2f}")
m3.metric("Precision at 0.5 threshold", f"{precision_score(test['target'], test_pred, zero_division=0):.2f}")
if test["target"].nunique() == 2:
    st.write(f"PR-AUC: **{average_precision_score(test['target'], test_prob):.3f}** · Brier score: **{brier_score_loss(test['target'], test_prob):.3f}**")
else:
    st.info("The held-out period contains only one target class, so PR-AUC is not available. Treat metrics cautiously.")
st.caption("Metrics are from a chronological holdout but are not proof of generalization. Compare against a seasonal/historical baseline before making performance claims.")

# For a one-week-ahead estimate, use the latest available row's lagged features.
latest = feat.iloc[[-1]].copy()
if latest[features].isna().any(axis=None):
    st.warning("The latest row lacks lagged inputs, so a current forecast cannot be produced.")
else:
    risk = float(model.predict_proba(latest[features])[0, 1])
    st.subheader("Next-week elevated-activity estimate")
    st.metric("Estimated probability", f"{risk:.0%}")
    st.progress(min(max(risk, 0.0), 1.0))
    st.caption("This is the model's probability of the prototype label, not the probability of a medically confirmed outbreak.")

importance = pd.DataFrame({"feature": features, "importance": model.feature_importances_}).sort_values("importance", ascending=False).head(10)
st.subheader("What the model used")
st.plotly_chart(px.bar(importance.sort_values("importance"), x="importance", y="feature", orientation="h", title="Random Forest feature importance"), use_container_width=True)
st.caption("Feature importance is not causal evidence. It describes model usage, not proof that a weather variable caused disease activity.")

with st.expander("Data quality and responsible use"):
    st.write("- Confirm case definitions and geographic level in the original source.")
    st.write("- Do not mix monthly and weekly observations or duplicate dates.")
    st.write("- Use only weather data available at the forecast issuance date.")
    st.write("- Test on later dates; compare with a baseline; report uncertainty.")
    st.write("- Do not use this prototype to make clinical decisions or declare outbreaks.")
