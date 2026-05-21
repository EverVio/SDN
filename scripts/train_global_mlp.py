"""
Phase 2: Train Global MLP model.

Loads global_features.pkl, trains MLPRegressor with StandardScaler,
evaluates on temporal hold-out, and saves the model bundle.
Output: models/global_mlp_model.pkl
"""

import os
import joblib
import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error

DATA_FILE = "../data/global_features.pkl"
MODEL_DIR = "../models"
MODEL_FILE = os.path.join(MODEL_DIR, "global_mlp_model.pkl")


def train_model():
    data = joblib.load(DATA_FILE)
    X = data["X"]
    Y = data["Y"]
    link_keys = data["link_keys"]
    window_size = data["window_size"]

    print(f"Loaded features: X={X.shape}, Y={Y.shape}")
    print(f"Links: {len(link_keys)}, Window: {window_size}")

    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    Y_train, Y_test = Y[:split_idx], Y[split_idx:]

    print(f"Train: {len(X_train)}, Test: {len(X_test)}")

    scaler_X = StandardScaler()
    X_train_scaled = scaler_X.fit_transform(X_train)
    X_test_scaled = scaler_X.transform(X_test)

    scaler_Y = StandardScaler()
    Y_train_scaled = scaler_Y.fit_transform(Y_train)
    Y_test_scaled = scaler_Y.transform(Y_test)

    model = MLPRegressor(
        hidden_layer_sizes=(128, 64),
        activation="relu",
        solver="adam",
        alpha=0.01,
        max_iter=1000,
        tol=1e-5,
        early_stopping=True,
        n_iter_no_change=15,
        validation_fraction=0.15,
        random_state=42,
        verbose=True,
    )

    model.fit(X_train_scaled, Y_train_scaled)

    Y_pred_scaled = model.predict(X_test_scaled)
    Y_pred = scaler_Y.inverse_transform(Y_pred_scaled)
    Y_pred = np.clip(Y_pred, 0.0, 1.0)

    mse = mean_squared_error(Y_test, Y_pred)
    mae = mean_absolute_error(Y_test, Y_pred)

    print(f"\nTraining completed.")
    print(f"MSE: {mse:.6f}, MAE: {mae:.6f}")
    print(f"RMSE: {np.sqrt(mse):.6f}")
    print(f"Converged in {model.n_iter_} iterations")

    joblib.dump(
        {
            "model": model,
            "scaler_X": scaler_X,
            "scaler_Y": scaler_Y,
            "link_keys": link_keys,
            "window_size": window_size,
        },
        MODEL_FILE,
    )
    print(f"\nModel saved to {MODEL_FILE}")
    print(f"Input dim: {len(link_keys) * window_size}, Output dim: {len(link_keys)}")


if __name__ == "__main__":
    train_model()
