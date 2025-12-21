from AlgorithmImports import *
from transformers import BertTokenizer, BertForSequenceClassification
from torch import no_grad
import torch
import numpy as np

class QuantScoutNLPBacktest(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2025, 12, 20)
        self.SetCash(100000)
        
        # Your 23-ticker universe
        self.tickers = [
            "TSLA", "SNOW", "DUOL", "ORCL", "RDDT", "SHOP", "MU", "DASH", "ARM", "RKLB",
            "LEU", "OKLO", "RIVN", "CRWV", "CRCL", "TSM", "VST", "NVDA", "GOOGL",
            "PLTR", "AMD", "AAPL", "AMZN"
        ]
        
        self.high_vol_tickers = {"LEU", "OKLO", "CRWV", "CRCL", "RIVN"}
        self.max_positions = 6
        
        # Regime filter
        self.spy = self.AddEquity("SPY", Resolution.Daily).Symbol
        self.vix = self.AddData(CBOE, "VIX", Resolution.Daily).Symbol
        self.spy_sma = self.SMA(self.spy, 200)
        
        # Load finBERT (runs once — takes ~10–20 seconds first time)
        model_name = "ProsusAI/finbert"
        self.tokenizer = BertTokenizer.from_pretrained(model_name)
        self.model = BertForSequenceClassification.from_pretrained(model_name)
        self.model.eval()  # Inference mode
        
        for ticker in self.tickers:
            equity = self.AddEquity(ticker, Resolution.Daily).Symbol
            self.AddData(TiingoNews, equity)
            
        self.Debug(f"finBERT loaded! Initialized {len(self.tickers)} tickers — superior financial sentiment active!")

    def GetFinBertScore(self, text: str) -> float:
        if not text.strip():
            return 0.0
        
        inputs = self.tokenizer(
            text, 
            return_tensors="pt", 
            truncation=True, 
            max_length=512, 
            padding=True
        )
        
        with no_grad():
            outputs = self.model(**inputs)
        
        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
        positive = probs[0][0].item()
        negative = probs[0][1].item()
        
        return positive - negative  # -1 to +1 range

    def OnData(self, slice: Slice):
        if TiingoNews not in slice:
            return
        
        tiingo_data = slice.Get(TiingoNews)
        if not tiingo_data:
            return
        
        # Regime
        spy_price = slice.Bars.get(self.spy)
        vix_data = slice.Get(CBOE).get(self.vix) if CBOE in slice else None
        bull_regime = spy_price and self.spy_sma.IsReady and spy_price.Close > self.spy_sma.Current.Value
        bear_high_vol = spy_price and self.spy_sma.IsReady and spy_price.Close < self.spy_sma.Current.Value and vix_data and vix_data.Close > 25
        
        ticker_signals = {}
        
        for news_symbol, article in tiingo_data.items():
            ticker = news_symbol.Underlying.Value
            if ticker not in self.tickers:
                continue
                
            text = f"{article.Title or ''} {article.Description or ''}".strip()
            if not text:
                continue
                
            score = self.GetFinBertScore(text)
            
            if ticker not in ticker_signals:
                ticker_signals[ticker] = []
            ticker_signals[ticker].append(score)
        
        for ticker, scores in ticker_signals.items():
            avg_sentiment = np.mean(scores)
            count = len(scores)
            
            if abs(avg_sentiment) > 0.3 or count > 2:
                label = "STRONG POS" if avg_sentiment > 0.5 else "POS" if avg_sentiment > 0 else "NEG" if avg_sentiment < -0.3 else "NEUTRAL"
                self.Debug(f"{ticker} @ {slice.Time}: finBERT {label} {avg_sentiment:+.3f} ({count} articles)")
            
            target = 0.0
            
            if avg_sentiment > 0.3 and bull_regime:
                if abs(avg_sentiment) > 0.7:
                    base = 0.15
                elif abs(avg_sentiment) > 0.5:
                    base = 0.12
                else:
                    base = 0.08
                
                if ticker in self.high_vol_tickers and abs(avg_sentiment) > 0.6:
                    base = 0.20
                
                target = base
                
            elif avg_sentiment < -0.45 and bear_high_vol:
                target = -0.08
            
            ticker_signals[ticker] = target
        
        # Max positions
        invested = sum(1 for h in self.Portfolio.Values if h.Invested)
        if invested >= self.max_positions:
            for ticker in ticker_signals:
                if ticker_signals[ticker] != 0 and not self.Portfolio[ticker].Invested:
                    ticker_signals[ticker] = 0.0
        
        # Execute
        for ticker, weight in ticker_signals.items():
            if weight != 0:
                self.SetHoldings(ticker, weight)
            elif self.Portfolio[ticker].Invested and ticker_signals.get(ticker, 0) == 0:
                self.Liquidate(ticker)
