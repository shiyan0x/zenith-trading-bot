"""
image_recognition.py — Computer Vision Chart Image Recognition Module (Optional).

Provides the standard interface and architecture for convolutional neural network (CNN)
pattern classification directly from rendered candlestick chart images.

DATA INTEGRITY NOTICE:
As per Phase 3 specifications, Zenith does NOT fabricate synthetic image labels or
claim a computer vision model is trained when verified labeled real-market datasets
are unavailable. This module provides:
1. Standardized Candlestick Image Rendering (deterministic numpy matrix)
2. PyTorch CNN Classifier Architecture (with NO_PATTERN / uncertain class)
3. Formal Dataset Specifications (chronological non-overlapping windows, calibration)
4. Fallback and inference interface ready for model weights loading
"""

import os
import json
import logging
from typing import List, Optional, Tuple, Dict, Any
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F

from src.core.market_feed import Candle
from src.patterns.pattern_definitions import (
    PatternMatch,
    PatternType,
    PatternStatus,
    PatternDirection,
)

logger = logging.getLogger(__name__)

# Standard pattern classification targets (including NO_PATTERN)
IMAGE_PATTERN_CLASSES = [
    "NO_PATTERN",
    "Double Top",
    "Double Bottom",
    "Head and Shoulders",
    "Inverse Head and Shoulders",
    "Bullish Flag",
    "Bearish Flag",
    "Bullish Pennant",
    "Bearish Pennant",
    "Ascending Triangle",
    "Descending Triangle",
    "Symmetrical Triangle",
    "Bullish Engulfing",
    "Bearish Engulfing",
    "Hammer",
    "Shooting Star",
    "Doji",
    "Morning Star",
    "Evening Star",
]


class CandlestickImageRenderer:
    """
    Renders a slice of Candle objects into a fixed-dimension numerical image tensor
    (Height x Width x Channels) without requiring external GUI frameworks.
    """

    def __init__(self, height: int = 64, width: int = 64):
        self.height = height
        self.width = width

    def render(self, candles: List[Candle]) -> np.ndarray:
        """
        Render a sequence of candles into a normalized [0, 1] 2D float array.
        Columns represent time (candles spaced evenly).
        Rows represent price (normalized between slice min low and max high).
        """
        if not candles:
            return np.zeros((self.height, self.width), dtype=np.float32)

        img = np.zeros((self.height, self.width), dtype=np.float32)
        n = len(candles)
        min_p = min(c.low for c in candles)
        max_p = max(c.high for c in candles)
        price_range = max(max_p - min_p, 1e-8)

        def norm_y(p: float) -> int:
            y = int((p - min_p) / price_range * (self.height - 1))
            return max(0, min(self.height - 1, y))

        x_coords = np.linspace(0, self.width - 1, n).astype(int)

        for i, c in enumerate(candles):
            x = x_coords[i]
            y_high = norm_y(c.high)
            y_low = norm_y(c.low)
            y_open = norm_y(c.open)
            y_close = norm_y(c.close)

            # Draw vertical wick
            for y in range(min(y_low, y_high), max(y_low, y_high) + 1):
                img[y, x] = max(img[y, x], 0.4)

            # Draw body (wider or brighter)
            body_bottom = min(y_open, y_close)
            body_top = max(y_open, y_close)
            val = 1.0 if c.close >= c.open else 0.7  # Distinguish bull/bear body intensity
            for y in range(body_bottom, body_top + 1):
                img[y, x] = val
                if x > 0:
                    img[y, x - 1] = max(img[y, x - 1], val * 0.8)
                if x < self.width - 1:
                    img[y, x + 1] = max(img[y, x + 1], val * 0.8)

        # Invert rows so low prices are at bottom, high prices at top
        return np.flipud(img).astype(np.float32)


class ChartPatternCNN(nn.Module):
    """
    Lightweight 3-layer Convolutional Neural Network for candlestick pattern classification.
    """

    def __init__(self, num_classes: int = len(IMAGE_PATTERN_CLASSES)):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 16, kernel_size=3, padding=1)
        self.pool1 = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(16, 32, kernel_size=3, padding=1)
        self.pool2 = nn.MaxPool2d(2, 2)
        self.conv3 = nn.Conv2d(32, 64, kernel_size=3, padding=1)
        self.pool3 = nn.MaxPool2d(2, 2)
        self.fc1 = nn.Linear(64 * 8 * 8, 128)
        self.dropout = nn.Dropout(0.3)
        self.fc2 = nn.Linear(128, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x shape: (B, 1, 64, 64)
        x = self.pool1(F.relu(self.conv1(x)))
        x = self.pool2(F.relu(self.conv2(x)))
        x = self.pool3(F.relu(self.conv3(x)))
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = self.dropout(x)
        logits = self.fc2(x)
        return logits


class ChartImagePatternRecognizer:
    """
    High-level Computer Vision Interface for Chart Image Pattern Recognition.
    Maintains rigorous status reporting: will NOT claim to be trained unless
    verified model weights exist.
    """

    def __init__(
        self,
        weights_path: Optional[str] = None,
        confidence_threshold: float = 0.65,
    ):
        self.weights_path = weights_path
        self.confidence_threshold = confidence_threshold
        self.classes = IMAGE_PATTERN_CLASSES
        self.renderer = CandlestickImageRenderer(height=64, width=64)
        self.model = ChartPatternCNN(num_classes=len(self.classes))
        self.is_trained = False

        if weights_path and os.path.exists(weights_path):
            try:
                state_dict = torch.load(weights_path, map_location=torch.device('cpu'))
                self.model.load_state_dict(state_dict)
                self.model.eval()
                self.is_trained = True
                logger.info(f"[CV PATTERN] Loaded model weights from {weights_path}")
            except Exception as e:
                logger.error(f"[CV PATTERN] Failed to load model weights: {e}")
                self.is_trained = False
        else:
            self.model.eval()
            self.is_trained = False
            logger.info("[CV PATTERN] No pre-trained weights loaded. Operating in specification/interface mode.")

    def predict(
        self,
        candles: List[Candle],
        symbol: str = "BTCUSDT",
        timeframe: str = "15m",
    ) -> Optional[PatternMatch]:
        """
        Run inference on candlestick slice.
        Returns PatternMatch if high-confidence pattern detected, or None.
        """
        if not self.is_trained:
            # Honest fallback: un-trained network must not return speculative predictions
            return None

        img = self.renderer.render(candles)
        tensor = torch.from_numpy(img).unsqueeze(0).unsqueeze(0)  # Shape (1, 1, 64, 64)

        with torch.no_grad():
            logits = self.model(tensor)
            probs = F.softmax(logits, dim=1).numpy()[0]

        top_idx = int(np.argmax(probs))
        top_prob = float(probs[top_idx])
        pred_class = self.classes[top_idx]

        if pred_class == "NO_PATTERN" or top_prob < self.confidence_threshold:
            return None

        # Return standardized PatternMatch
        current_candle = candles[-1]
        return PatternMatch(
            pattern_name=pred_class,
            pattern_type=PatternType.REVERSAL if "Top" in pred_class or "Bottom" in pred_class else PatternType.CONTINUATION,
            direction=PatternDirection.BULLISH if "Bullish" in pred_class or "Bottom" in pred_class or "Morning" in pred_class else PatternDirection.BEARISH,
            symbol=symbol,
            timeframe=timeframe,
            detection_timestamp=current_candle.timestamp,
            candle_range=(0, len(candles) - 1),
            timestamps=(candles[0].timestamp, current_candle.timestamp),
            status=PatternStatus.CONFIRMED,
            key_levels={
                'close': current_candle.close,
                'high': current_candle.high,
                'low': current_candle.low,
            },
            confidence_score=round(top_prob, 3),
            supporting_evidence={
                'cv_model_version': '1.0.0',
                'class_probabilities': {self.classes[i]: round(float(probs[i]), 3) for i in range(len(self.classes)) if probs[i] > 0.05}
            },
            detection_method="cv_image_cnn",
        )

    def get_training_specification(self) -> Dict[str, Any]:
        """Returns the rigorous specifications required to train this module."""
        return {
            "model_architecture": "3-layer Conv2D (16, 32, 64 filters) + Dropout(0.3) + Linear(128)",
            "input_resolution": "64x64 grayscale float32 normalized [0.0, 1.0]",
            "classes": self.classes,
            "classes_count": len(self.classes),
            "data_splitting": "Strict chronological split (70% train, 15% validation, 15% untouched test)",
            "leakage_prevention": "Zero overlapping windows between sets; 30-candle gap between split boundaries",
            "evaluation_metrics": ["Precision", "Recall", "F1-Score", "Confusion Matrix per class"],
            "calibration_method": "Platt scaling / Temperature scaling on holdout validation set",
            "is_trained": self.is_trained,
            "weights_path": self.weights_path,
        }
