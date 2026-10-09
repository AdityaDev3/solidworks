from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (average_precision_score, brier_score_loss,
                             precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import make_pipeline

from .db import BASE_DIR, db

ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", str(BASE_DIR / "artifacts")))
if not ARTIFACT_DIR.is_absolute(): ARTIFACT_DIR = BASE_DIR / ARTIFACT_DIR
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
FEATURES = ["cases_lag1", "cases_mean4", "rain_28d", "temp_mean_28d", "humidity_mean_28d", "month_sin", "month_cos", "cases_per_100k"]


def _observations(location_id: str | None = None) -> pd.DataFrame:
    where, args = "", []
    if location_id: where, args = " WHERE location_id=?", [location_id]
    with db() as cx:
        rows = cx.execute(f"SELECT location_id,category,observed_on,observed_to,value,unit,source,quality,metadata FROM observations{where} ORDER BY observed_on", args).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def build_weekly_records(location_id: str | None = None) -> pd.DataFrame:
    raw = _observations(location_id)
    if raw.empty: return pd.DataFrame()
    disease = raw[(raw.category == "dengue_cases") & raw.observed_to.notna()].copy()
    disease["interval_days"] = (pd.to_datetime(disease.observed_to) - pd.to_datetime(disease.observed_on)).dt.days + 1
    disease = disease[disease.interval_days.between(6, 8)].copy()
    if disease.empty: return pd.DataFrame()
    disease["date"] = pd.to_datetime(disease.observed_on)
    weather = raw[raw.category.isin(["rainfall", "temperature_mean", "humidity"])].copy()
    if not weather.empty: weather["date"] = pd.to_datetime(weather.observed_on)
    population = raw[raw.category == "population_count"].copy()
    if not population.empty: population["date"] = pd.to_datetime(population.observed_on)
    records = []
    for loc_id, loc_cases in disease.groupby("location_id"):
        loc_cases = loc_cases.sort_values("date").reset_index(drop=True)
        loc_weather = weather[weather.location_id == loc_id] if not weather.empty else weather
        loc_pop = population[population.location_id == loc_id] if not population.empty else population
        values = pd.to_numeric(loc_cases.value, errors="coerce")
        for i, case in loc_cases.iterrows():
            origin = pd.Timestamp(case.observed_to)
            prior = values.iloc[max(0, i-3):i+1]
            row = {"location_id":loc_id,"origin":origin,"cases":float(case.value),"cases_lag1":float(case.value),
                   "cases_mean4":float(prior.mean()),"month_sin":float(np.sin(2*np.pi*origin.month/12)),
                   "month_cos":float(np.cos(2*np.pi*origin.month/12)),"cases_per_100k":np.nan}
            window_start = origin - pd.Timedelta(days=27)
            if not loc_weather.empty:
                window = loc_weather[(loc_weather.date > window_start) & (loc_weather.date <= origin)]
                for category, feature, agg in [("rainfall","rain_28d","sum"),("temperature_mean","temp_mean_28d","mean"),("humidity","humidity_mean_28d","mean")]:
                    vals = pd.to_numeric(window.loc[window.category == category,"value"],errors="coerce").dropna()
                    # Require at least 21 of the preceding 28 daily observations; missingness is not zero.
                    row[feature] = float(vals.sum() if agg == "sum" else vals.mean()) if len(vals) >= 21 else np.nan
            else:
                row.update({"rain_28d":np.nan,"temp_mean_28d":np.nan,"humidity_mean_28d":np.nan})
            if not loc_pop.empty:
                eligible = loc_pop[loc_pop.date <= origin]
                if not eligible.empty and case.value is not None:
                    p = eligible.sort_values("date").iloc[-1].value
                    if float(p) > 0: row["cases_per_100k"] = float(case.value) / float(p) * 100_000
            next_four = None
            if i + 4 < len(loc_cases):
                following=loc_cases.iloc[i+1:i+5]
                gaps=[(pd.Timestamp(following.iloc[j].observed_on)-pd.Timestamp(case.observed_on)).days for j in range(4)]
                if gaps == [7,14,21,28]: next_four=float(pd.to_numeric(following.value,errors="coerce").max())
            row["target_cases_next_4w"] = next_four
            records.append(row)
    result = pd.DataFrame(records)
    return result.sort_values(["origin","location_id"]).reset_index(drop=True) if not result.empty else result


def train_model(location_id: str) -> dict:
    if not location_id: raise ValueError("Choose a district; pooled case counts across differently sized locations are not used.")
    data = build_weekly_records(location_id)
    if data.empty: raise ValueError("No compatible weekly dengue observations are available; no training records were fabricated.")
    supervised = data.dropna(subset=["target_cases_next_4w"]).copy()
    dates = sorted(supervised.origin.unique())
    if len(dates) < 120:
        raise ValueError(f"Only {len(dates)} weekly origins are available; training requires at least 120 for chronological four-week train/calibration/test blocks.")
    train_cut, cal_cut = dates[int(len(dates)*0.70)-1], dates[int(len(dates)*0.85)-1]
    fit_data = supervised[supervised.origin <= train_cut].copy()
    calibration = supervised[(supervised.origin > train_cut) & (supervised.origin <= cal_cut)].copy()
    test = supervised[supervised.origin > cal_cut].copy()
    if len(calibration) < 10 or len(test) < 10: raise ValueError("Not enough independent calibration or later holdout records.")
    threshold = float(fit_data.cases.quantile(0.75))
    y_fit = (fit_data.target_cases_next_4w > threshold).astype(int)
    y_cal = (calibration.target_cases_next_4w > threshold).astype(int)
    y_test = (test.target_cases_next_4w > threshold).astype(int)
    if y_fit.nunique() < 2 or y_cal.nunique() < 2:
        raise ValueError("Training and calibration windows do not contain both outcome classes; prediction remains unavailable.")
    base = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), RandomForestClassifier(
        n_estimators=500, max_depth=7, min_samples_leaf=4, class_weight="balanced_subsample", random_state=42, n_jobs=-1))
    base.fit(fit_data[FEATURES],y_fit)
    calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="sigmoid").fit(calibration[FEATURES],y_cal)
    probs = calibrated.predict_proba(test[FEATURES])[:,1]
    preds = (probs >= 0.5).astype(int)
    prevalence = float(y_fit.mean())
    metrics = {"test_records":int(len(test)),"train_records":int(len(fit_data)),"calibration_records":int(len(calibration)),
               "test_prevalence":float(y_test.mean()),"training_threshold_cases":threshold,
               "model_pr_auc":float(average_precision_score(y_test,probs)) if y_test.nunique()>1 else None,
               "baseline_pr_auc":float(y_test.mean()),"brier_score":float(brier_score_loss(y_test,probs)),
               "baseline_brier_score":float(brier_score_loss(y_test,np.full(len(y_test),prevalence))),
               "precision_at_0_5":float(precision_score(y_test,preds,zero_division=0)),
               "recall_at_0_5":float(recall_score(y_test,preds,zero_division=0)),
               "roc_auc":float(roc_auc_score(y_test,probs)) if y_test.nunique()>1 else None,
               "target":"at least one of the next four contiguous weekly dengue counts exceeds the training-window 75th percentile",
               "forecast_horizon":"next four weekly reporting intervals","split":"chronological 70/15/15; last 15% is untouched test"}
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = ARTIFACT_DIR / f"dengue-risk-{version}.joblib"
    artifact_data = {"model":calibrated,"features":FEATURES,"threshold":threshold,"version":version,"location_id":location_id,"metrics":metrics,
                    "training_end":str(pd.Timestamp(train_cut).date()),"calibration_end":str(pd.Timestamp(cal_cut).date())}
    joblib.dump(artifact_data,artifact)
    with db() as cx:
        cx.execute("INSERT INTO model_runs(version,location_id,trained_at,cutoff,training_start,training_end,metrics,feature_names,artifact_path,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (version,location_id,datetime.now(timezone.utc).isoformat(),str(pd.Timestamp(cal_cut).date()),str(supervised.origin.min().date()),str(pd.Timestamp(train_cut).date()),json.dumps(metrics),json.dumps(FEATURES),str(artifact),"ready"))
    return {"version":version,"metrics":metrics,"artifact":artifact.name}


def latest_model(location_id: str | None=None) -> dict | None:
    with db() as cx:
        if location_id: row = cx.execute("SELECT * FROM model_runs WHERE status='ready' AND location_id=? ORDER BY id DESC LIMIT 1",(location_id,)).fetchone()
        else: row = cx.execute("SELECT * FROM model_runs WHERE status='ready' ORDER BY id DESC LIMIT 1").fetchone()
    if not row: return None
    artifact = joblib.load(row["artifact_path"])
    return artifact


def latest_risk(location_id: str) -> dict:
    artifact = latest_model(location_id)
    if not artifact: return {"status":"unavailable","reason":"Train a model after suitable real weekly case and weather data have been ingested."}
    data = build_weekly_records(location_id)
    if data.empty: return {"status":"unavailable","reason":"No weekly disease and climate records for this area."}
    current = data.sort_values("origin").iloc[-1]
    if current.origin.date() < date.today() - timedelta(days=28):
        return {"status":"unavailable","reason":"The latest usable dengue report is more than 28 days old; refresh surveillance data before forecasting."}
    required = ["cases_lag1","cases_mean4","rain_28d","temp_mean_28d","humidity_mean_28d"]
    if any(pd.isna(current.get(f)) for f in required): return {"status":"unavailable","reason":"Recent case or weather inputs are incomplete for this forecast."}
    x = pd.DataFrame([{key:current.get(key,np.nan) for key in FEATURES}])
    score = float(artifact["model"].predict_proba(x)[0,1])
    level = "Higher likelihood" if score >= .5 else "Lower likelihood"
    explanation = {"last_observed_week_end":str(current.origin.date()),"target":"next contiguous weekly interval",
                   "signals":{"rainfall_28d_mm":current.rain_28d,"temperature_28d_mean_c":current.temp_mean_28d,
                              "humidity_28d_mean_pct":current.humidity_mean_28d,"latest_week_cases":current.cases},
                   "probability":score,"calibrated":True,"threshold_cases":artifact["threshold"],
                   "warnings":["Prototype estimate; not an official outbreak declaration.","Calibration quality depends on the available historical source and district coverage."]}
    record_prediction(location_id,artifact["version"],score,level,explanation,
                      str((current.origin+pd.Timedelta(days=1)).date()),str((current.origin+pd.Timedelta(days=28)).date()))
    return {"status":"available","location_id":location_id,"score":score,"risk_level":level,"model_version":artifact["version"],"period_start":str((current.origin+pd.Timedelta(days=1)).date()),"period_end":str((current.origin+pd.Timedelta(days=28)).date()),"explanation":explanation}


def record_prediction(location_id: str, version: str, score: float, level: str, explanation: dict, period_start: str, period_end: str):
    with db() as cx:
        existing=cx.execute("SELECT 1 FROM predictions WHERE location_id=? AND period_start=? AND period_end=? AND model_version=? LIMIT 1",(location_id,period_start,period_end,version)).fetchone()
        if existing: return
        cx.execute("INSERT INTO predictions(location_id,prediction_at,period_start,period_end,score,risk_level,model_version,explanation,quality) VALUES(?,?,?,?,?,?,?,?,?)",
                   (location_id,datetime.now(timezone.utc).isoformat(),period_start,period_end,score,level,version,json.dumps(explanation),"experimental_calibrated_estimate"))


def global_feature_importance(location_id: str | None=None) -> list[dict]:
    artifact = latest_model(location_id)
    if not artifact: return []
    pipe = artifact["model"].estimator
    forest = pipe.named_steps["randomforestclassifier"]
    names = pipe[:-1].get_feature_names_out(FEATURES)
    return [{"feature":name,"importance":float(value)} for name,value in sorted(zip(names,forest.feature_importances_),key=lambda pair:pair[1],reverse=True)]
