from enum import Enum
from functools import wraps
import pandas as pd
import numpy as np
from pandas import DataFrame, Series
from datetime import datetime

# Docs: https://github.com/joshyattridge/smart-money-concepts/blob/master/README.md


def inputvalidator(input_="ohlc"):
    def dfcheck(func):
        @wraps(func)
        def wrap(*args, **kwargs):
            args = list(args)
            i = 0 if isinstance(args[0], pd.DataFrame) else 1

            args[i] = args[i].rename(
                columns={c: c.lower() for c in args[i].columns})

            inputs = {
                "o": "open",
                "h": "high",
                "l": "low",
                "c": kwargs.get("column", "close").lower(),
                "v": "volume",
            }

            if inputs["c"] != "close":
                kwargs["column"] = inputs["c"]

            for l in input_:
                if inputs[l] not in args[i].columns:
                    raise LookupError(
                        'Must have a dataframe column named "{0}"'.format(
                            inputs[l])
                    )

            return func(*args, **kwargs)

        return wrap

    return dfcheck


def apply(decorator):
    def decorate(cls):
        for attr in cls.__dict__:
            if callable(getattr(cls, attr)):
                setattr(cls, attr, decorator(getattr(cls, attr)))

        return cls

    return decorate


@apply(inputvalidator(input_="ohlc"))
class smc:
    __version__ = "0.0.21"

    @classmethod
    def fvg(cls, ohlc: DataFrame, join_consecutive=False) -> Series:
        """
        FVG - Fair Value Gap
        A fair value gap is when the previous high is lower than the next low if the current candle is bullish.
        Or when the previous low is higher than the next high if the current candle is bearish.

        parameters:
        join_consecutive: bool - if there are multiple FVG in a row then they will be merged into one using the highest top and the lowest bottom

        returns:
        FVG = 1 if bullish fair value gap, -1 if bearish fair value gap
        Top = the top of the fair value gap
        Bottom = the bottom of the fair value gap
        MitigatedIndex = the index of the candle that mitigated the fair value gap
        """

        fvg = np.where(
            (
                (ohlc["high"].shift(2) < ohlc["low"])
                & (ohlc["close"] > ohlc["open"])
            )
            | (
                (ohlc["low"].shift(2) > ohlc["high"])
                & (ohlc["close"] < ohlc["open"])
            ),
            np.where(ohlc["close"] > ohlc["open"], 1, -1),
            np.nan,
        )

        top = np.where(
            ~np.isnan(fvg),
            np.where(
                ohlc["close"] > ohlc["open"],
                ohlc["low"],
                ohlc["low"].shift(2),
            ),
            np.nan,
        )

        bottom = np.where(
            ~np.isnan(fvg),
            np.where(
                ohlc["close"] > ohlc["open"],
                ohlc["high"].shift(2),
                ohlc["high"],
            ),
            np.nan,
        )

        # if there are multiple consecutive fvg then join them together using the highest top and lowest bottom and the last index
        if join_consecutive:
            for i in range(len(fvg) - 1):
                if fvg[i] == fvg[i + 1]:
                    top[i + 1] = max(top[i], top[i + 1])
                    bottom[i + 1] = min(bottom[i], bottom[i + 1])
                    fvg[i] = top[i] = bottom[i] = np.nan

        mitigated_index = np.zeros(len(ohlc), dtype=np.int32)
        for i in np.where(~np.isnan(fvg))[0]:
            mask = np.zeros(len(ohlc), dtype=np.bool_)
            if fvg[i] == 1:
                mask = ohlc["low"][i + 2:] <= top[i]
            elif fvg[i] == -1:
                mask = ohlc["high"][i + 2:] >= bottom[i]
            if np.any(mask):
                j = np.argmax(mask) + i + 2
                mitigated_index[i] = j
        mitigated_index = np.where(np.isnan(fvg), np.nan, mitigated_index)
        return pd.concat([
            pd.Series(fvg, name="FVG"),
            pd.Series(top, name="Top"),
            pd.Series(bottom, name="Bottom"),
            pd.Series(mitigated_index, name="MitigatedIndex"),
        ],
            axis=1,
        )

    class SwingMethodEvaluator(Enum):
        COMBINED = "combined"
        FRACTALS = "fractals"
        MOMENTUM = "momentum"
        WEIGHTED_ROLLING_WINDOW = "weighted_rolling_window"
        DEFAULT = "default"

    @classmethod
    def swing_highs_lows(cls, ohlc: DataFrame, swing_evaluator: SwingMethodEvaluator = SwingMethodEvaluator.DEFAULT,
                         swing_length: int = 10, short_swing_length: int = 10, long_swing_length=50) -> Series:
        """
        Swing Highs and Lows without lookahead bias.

        A swing high is when the current high is the highest high out of 
        the last `swing_length` candles (including itself).
        A swing low is when the current low is the lowest low out of 
        the last `swing_length` candles (including itself).

        parameters:
        swing_length: int - Number of candles to look back to determine swings.
        short_swing_length: int - Number of candles for short-term swings.
        long_swing_length: int - Number of candles for long-term swings.

        returns:
        HighLow = 1 if swing high, -1 if swing low
        Level = the level of the swing high or low
        """
        def fractals():
            """
            Identifies swing highs/lows based on a smaller subset of candles rather than a fixed-length rolling window.
            
            How it helps: Fractals use fewer candles (e.g., just the previous two and the next two), which reduces lag.

            Trade-off: Less robust for noisy markets.
            """
            swing_highs = np.where(
                (ohlc["high"] > ohlc["high"].shift(1)) &
                (ohlc["high"] > ohlc["high"].shift(2)) &
                (ohlc["high"] > ohlc["high"].rolling(window=swing_length, center=False).max().shift(1)),
                1,
                np.nan,
            )
            swing_lows = np.where(
                (ohlc["low"] < ohlc["low"].shift(1)) &
                (ohlc["low"] < ohlc["low"].shift(2)) &
                (ohlc["low"] < ohlc["low"].rolling(window=swing_length, center=False).min().shift(1)),
                -1,
                np.nan,
            )
            # Combine swing highs and lows
            swing_highs_lows = np.nan_to_num(
                swing_highs) + np.nan_to_num(swing_lows)

            # Determine swing levels
            level = np.where(
                swing_highs_lows == 1,
                ohlc["high"],  # Level for swing highs
                # Level for swing lows
                np.where(swing_highs_lows == -1, ohlc["low"], np.nan),
            )
            return swing_highs_lows, level

        def momentum():
            """
            Use momentum (e.g., rate of change or RSI) to confirm swing points earlier by identifying
            trends or overbought/oversold conditions.
            
            How it helps: This can confirm swing points without waiting for the full swing_length.
            """

            ohlc["momentum"] = ohlc["close"].diff(swing_length)
            swing_highs = np.where(
                (ohlc["high"] == ohlc["high"].rolling(window=swing_length).max()) &
                (ohlc["momentum"] > 0),
                1,
                np.nan,
            )
            swing_lows = np.where(
                (ohlc["low"] == ohlc["low"].rolling(window=swing_length).min()) &
                (ohlc["momentum"] < 0),
                -1,
                np.nan,
            )
            # Combine swing highs and lows
            swing_highs_lows = np.nan_to_num(
                swing_highs) + np.nan_to_num(swing_lows)

            # Determine swing levels
            level = np.where(
                swing_highs_lows == 1,
                ohlc["high"],  # Level for swing highs
                # Level for swing lows
                np.where(swing_highs_lows == -1, ohlc["low"], np.nan),
            )
            return swing_highs_lows, level

        def weighted_rolling_window():
            """
            Instead of treating all candles equally in the rolling window, give more weight to recent candles.

            How it helps: This makes the indicator react faster to recent price movements.

            Trade-off: Swings may not strictly align with traditional high/low definitions.
            """

            ohlc["high_ema"] = ohlc["high"].ewm(span=swing_length).mean()
            ohlc["low_ema"] = ohlc["low"].ewm(span=swing_length).mean()

            swing_highs = np.where(ohlc["high"] >= ohlc["high_ema"], 1, np.nan)
            swing_lows = np.where(ohlc["low"] <= ohlc["low_ema"], -1, np.nan)
            # Combine swing highs and lows
            swing_highs_lows = np.nan_to_num(
                swing_highs) + np.nan_to_num(swing_lows)

            # Determine swing levels
            level = np.where(
                swing_highs_lows == 1,
                ohlc["high"],  # Level for swing highs
                # Level for swing lows
                np.where(swing_highs_lows == -1, ohlc["low"], np.nan),
            )
            return swing_highs_lows, level

        def combined():
            """
            Combine Short and Long Swing Lengths for Swing Highs and Lows.

            Parameters:
            ohlc: DataFrame - Contains columns 'high' and 'low'.
            short_swing_length: int - Number of candles for short-term swings.
            long_swing_length: int - Number of candles for long-term swings.
            """

            # Short-term swing highs and lows
            short_highs = ohlc["high"].rolling(
                window=short_swing_length, min_periods=1).max()
            short_lows = ohlc["low"].rolling(
                window=short_swing_length, min_periods=1).min()

            short_swing_highs = np.where(
                ohlc["high"] == short_highs, 1, np.nan)
            short_swing_lows = np.where(ohlc["low"] == short_lows, -1, np.nan)

            # Long-term swing highs and lows
            long_highs = ohlc["high"].rolling(
                window=long_swing_length, min_periods=1).max()
            long_lows = ohlc["low"].rolling(
                window=long_swing_length, min_periods=1).min()

            long_swing_highs = np.where(ohlc["high"] == long_highs, 2, np.nan)
            long_swing_lows = np.where(ohlc["low"] == long_lows, -2, np.nan)

            # Combine short-term and long-term swings
            swing_highs_lows = (
                np.nan_to_num(short_swing_highs) +
                np.nan_to_num(short_swing_lows) +
                np.nan_to_num(long_swing_highs) +
                np.nan_to_num(long_swing_lows)
            )

            # Determine swing levels
            level = np.where(
                swing_highs_lows == 1, ohlc["high"],  # Short-term high
                np.where(
                    swing_highs_lows == -1, ohlc["low"],  # Short-term low
                    np.where(
                        swing_highs_lows == 2, ohlc["high"],  # Long-term high
                        np.where(swing_highs_lows == -2,
                                 ohlc["low"], np.nan)  # Long-term low
                    )
                )
            )
            return swing_highs_lows, level

        def default():
            """
            Swing Highs and Lows without lookahead bias.

            A swing high is when the current high is the highest high out of 
            the last `swing_length` candles (including itself).
            A swing low is when the current low is the lowest low out of 
            the last `swing_length` candles (including itself).
            """

            # Calculate swing highs
            swing_highs = np.where(
                ohlc["high"] == ohlc["high"].rolling(
                    window=swing_length, min_periods=1).max(),
                1,
                np.nan,
            )

            # Calculate swing lows
            swing_lows = np.where(
                ohlc["low"] == ohlc["low"].rolling(
                    window=swing_length, min_periods=1).min(),
                -1,
                np.nan,
            )

            # Combine swing highs and lows
            swing_highs_lows = np.nan_to_num(
                swing_highs) + np.nan_to_num(swing_lows)

            # Determine swing levels
            level = np.where(
                swing_highs_lows == 1,
                ohlc["high"],  # Level for swing highs
                # Level for swing lows
                np.where(swing_highs_lows == -1, ohlc["low"], np.nan),
            )
            return swing_highs_lows, level

        match swing_evaluator.value:
            case "momentum":
                swing_highs_lows, level = momentum()
            case "weighted_rolling_window":
                swing_highs_lows, level = weighted_rolling_window()
            case "fractals":
                swing_highs_lows, level = fractals()
            case "combined":
                swing_highs_lows, level = combined()
            case "default":
                swing_highs_lows, level = default()
            case _:
                swing_highs_lows, level = default()

        # Return results as a DataFrame
        return pd.DataFrame({
            "HighLow": swing_highs_lows,
            "Level": level,
        })

    @classmethod
    def bos_choch(
        cls, ohlc: DataFrame, swing_highs_lows: DataFrame, close_break: bool = True
    ) -> Series:
        """
        BOS - Break of Structure
        CHoCH - Change of Character
        these are both indications of market structure changing

        parameters:
        swing_highs_lows: DataFrame - provide the dataframe from the swing_highs_lows function
        close_break: bool - if True then the break of structure will be mitigated based on the close of the candle otherwise it will be the high/low.

        returns:
        BOS = 1 if bullish break of structure, -1 if bearish break of structure
        CHOCH = 1 if bullish change of character, -1 if bearish change of character
        Level = the level of the break of structure or change of character
        BrokenIndex = the index of the candle that broke the level
        """

        swing_highs_lows = swing_highs_lows.copy()

        level_order = []
        highs_lows_order = []

        bos = np.zeros(len(ohlc), dtype=np.int32)
        choch = np.zeros(len(ohlc), dtype=np.int32)
        level = np.zeros(len(ohlc), dtype=np.float32)

        last_positions = []

        for i in range(len(swing_highs_lows["HighLow"])):
            if not np.isnan(swing_highs_lows["HighLow"][i]):
                level_order.append(swing_highs_lows["Level"][i])
                highs_lows_order.append(swing_highs_lows["HighLow"][i])
                if len(level_order) >= 4:
                    # bullish bos
                    bos[last_positions[-2]] = (
                        1
                        if (
                            np.all(highs_lows_order[-4:] == [-1, 1, -1, 1])
                            and np.all(
                                level_order[-4]
                                < level_order[-2]
                                < level_order[-3]
                                < level_order[-1]
                            )
                        )
                        else 0
                    )
                    level[last_positions[-2]] = (
                        level_order[-3] if bos[last_positions[-2]] != 0 else 0
                    )

                    # bearish bos
                    bos[last_positions[-2]] = (
                        -1
                        if (
                            np.all(highs_lows_order[-4:] == [1, -1, 1, -1])
                            and np.all(
                                level_order[-4]
                                > level_order[-2]
                                > level_order[-3]
                                > level_order[-1]
                            )
                        )
                        else bos[last_positions[-2]]
                    )
                    level[last_positions[-2]] = (
                        level_order[-3] if bos[last_positions[-2]] != 0 else 0
                    )

                    # bullish choch
                    choch[last_positions[-2]] = (
                        1
                        if (
                            np.all(highs_lows_order[-4:] == [-1, 1, -1, 1])
                            and np.all(
                                level_order[-1]
                                > level_order[-3]
                                > level_order[-4]
                                > level_order[-2]
                            )
                        )
                        else 0
                    )
                    level[last_positions[-2]] = (
                        level_order[-3]
                        if choch[last_positions[-2]] != 0
                        else level[last_positions[-2]]
                    )

                    # bearish choch
                    choch[last_positions[-2]] = (
                        -1
                        if (
                            np.all(highs_lows_order[-4:] == [1, -1, 1, -1])
                            and np.all(
                                level_order[-1]
                                < level_order[-3]
                                < level_order[-4]
                                < level_order[-2]
                            )
                        )
                        else choch[last_positions[-2]]
                    )
                    level[last_positions[-2]] = (
                        level_order[-3]
                        if choch[last_positions[-2]] != 0
                        else level[last_positions[-2]]
                    )

                last_positions.append(i)

        broken = np.zeros(len(ohlc), dtype=np.int32)
        for i in np.where(np.logical_or(bos != 0, choch != 0))[0]:
            mask = np.zeros(len(ohlc), dtype=np.bool_)
            # if the bos is 1 then check if the candles high has gone above the level
            if bos[i] == 1 or choch[i] == 1:
                mask = ohlc["close" if close_break else "high"][i + 2:] > level[i]
            # if the bos is -1 then check if the candles low has gone below the level
            elif bos[i] == -1 or choch[i] == -1:
                mask = ohlc["close" if close_break else "low"][i + 2:] < level[i]
            if np.any(mask):
                j = np.argmax(mask) + i + 2
                broken[i] = j
                # if there are any unbroken bos or choch that started before this one and ended after this one then remove them
                for k in np.where(np.logical_or(bos != 0, choch != 0))[0]:
                    if k < i and broken[k] >= j:
                        bos[k] = 0
                        choch[k] = 0
                        level[k] = 0

        # remove the ones that aren't broken
        for i in np.where(
            np.logical_and(np.logical_or(bos != 0, choch != 0), broken == 0)
        )[0]:
            bos[i] = 0
            choch[i] = 0
            level[i] = 0

        # replace all the 0s with np.nan
        bos = np.where(bos != 0, bos, np.nan)
        choch = np.where(choch != 0, choch, np.nan)
        level = np.where(level != 0, level, np.nan)
        broken = np.where(broken != 0, broken, np.nan)

        bos = pd.Series(bos, name="BOS")
        choch = pd.Series(choch, name="CHOCH")
        level = pd.Series(level, name="Level")
        broken = pd.Series(broken, name="BrokenIndex")

        return pd.concat([bos, choch, level, broken], axis=1)

    @classmethod
    def ob(
        cls,
        ohlc: DataFrame,
        swing_highs_lows: DataFrame,
        close_mitigation: bool = False,
    ) -> DataFrame:
        """
        OB - Order Blocks
        This method detects order blocks when there is a high amount of market orders exist on a price range.

        parameters:
        swing_highs_lows: DataFrame - provide the dataframe from the swing_highs_lows function
        close_mitigation: bool - if True then the order block will be mitigated based on the close of the candle otherwise it will be the high/low.

        returns:
        OB = 1 if bullish order block, -1 if bearish order block
        Top = top of the order block
        Bottom = bottom of the order block
        OBVolume = volume + 2 last volumes amounts
        Percentage = strength of order block (min(highVolume, lowVolume)/max(highVolume,lowVolume))
        """
        swing_highs_lows = swing_highs_lows.copy()
        ohlc_len = len(ohlc)

        _open, _high, _low, _close, _volume = ohlc["open"], ohlc["high"], ohlc["low"], ohlc["close"], ohlc["volume"]
        _swing_high_low = swing_highs_lows["HighLow"]

        # Initialize arrays
        ob = np.zeros(ohlc_len, dtype=np.int32)
        top = np.zeros(ohlc_len, dtype=np.float32)
        bottom = np.zeros(ohlc_len, dtype=np.float32)
        obVolume = np.zeros(ohlc_len, dtype=np.float32)
        lowVolume = np.zeros(ohlc_len, dtype=np.float32)
        highVolume = np.zeros(ohlc_len, dtype=np.float32)
        percentage = np.zeros(ohlc_len, dtype=np.int32)
        breaker = np.full(ohlc_len, False, dtype=bool)
        crossed = np.full(ohlc_len, False, dtype=bool)
        mitigated_index = np.zeros(ohlc_len, dtype=np.int32)  # Initialize mitigated index

        def find_last_swing(index: int, direction: int) -> int:
            """Helper function to find last swing index based on direction (1 for high, -1 for low)."""
            last_swing_indices = np.where((_swing_high_low == direction) & (np.arange(ohlc_len) < index))[0]
            return np.max(last_swing_indices) if last_swing_indices.size > 0 else None

        def update_order_block(index: int, direction: int):
            nonlocal mitigated_index
            if direction == 1:  # Bullish OB
                obBtm, obTop = _low.iloc[index - 1], _high.iloc[index - 1]
            else:  # Bearish OB
                obBtm, obTop = _high.iloc[index - 1], _low.iloc[index - 1]
            
            obIndex = index - 1
            for j in range(1, index):
                if direction == 1:  # Bullish
                    obBtm = min(_low.iloc[j], obBtm)
                    if obBtm == _low.iloc[j]:
                        obTop = _high.iloc[j]
                else:  # Bearish
                    obTop = max(_high.iloc[j], obTop)
                    if obTop == _high.iloc[j]:
                        obBtm = _low.iloc[j]

                if direction == 1 and obBtm == _low.iloc[j]:
                    obIndex = j
                elif direction == -1 and obTop == _high.iloc[j]:
                    obIndex = j

            ob[obIndex] = direction
            top[obIndex], bottom[obIndex] = obTop, obBtm
            obVolume[obIndex] = sum(_volume.iloc[index - i] for i in range(3))  # Sum of last 3 volumes
            lowVolume[obIndex] = _volume.iloc[index - 2] if direction == 1 else _volume.iloc[index]
            highVolume[obIndex] = _volume.iloc[index] + _volume.iloc[index - 1] if direction == 1 else _volume.iloc[index - 2]
            percentage[obIndex] = (min(lowVolume[obIndex], highVolume[obIndex]) /
                                max(lowVolume[obIndex], highVolume[obIndex])) * 100.0 if max(lowVolume[obIndex], highVolume[obIndex]) != 0 else 1

            # Update mitigated index when block is mitigated
            if breaker[obIndex] and mitigated_index[obIndex] == 0:
                mitigated_index[obIndex] = index - 1

        for i in range(ohlc_len):
            close_index = i

            # Process Bullish Order Block
            last_top_index = find_last_swing(close_index, 1)
            if last_top_index is not None and _close.iloc[close_index] > _high.iloc[last_top_index] and not crossed[last_top_index]:
                crossed[last_top_index] = True
                update_order_block(close_index, 1)

            # Process Bearish Order Block
            last_btm_index = find_last_swing(close_index, -1)
            if last_btm_index is not None and _close.iloc[close_index] < _low.iloc[last_btm_index] and not crossed[last_btm_index]:
                crossed[last_btm_index] = True
                update_order_block(close_index, -1)

        # Final adjustments
        ob = np.where(ob != 0, ob, np.nan)
        top, bottom, obVolume, mitigated_index, percentage = (np.where(~np.isnan(ob), arr, np.nan) for arr in [top, bottom, obVolume, mitigated_index, percentage])

        # Return as DataFrame
        return pd.DataFrame({
            "OB": ob,
            "Top": top,
            "Bottom": bottom,
            "OBVolume": obVolume,
            "MitigatedIndex": mitigated_index,
            "Percentage": percentage
        })


    @classmethod
    def liquidity(
        cls, ohlc: DataFrame, swing_highs_lows: DataFrame, range_percent: float = 0.01
    ) -> Series:
        """
        Liquidity
        Liquidity is when there are multiply highs within a small range of each other.
        or multiply lows within a small range of each other.

        parameters:
        swing_highs_lows: DataFrame - provide the dataframe from the swing_highs_lows function
        range_percent: float - the percentage of the range to determine liquidity

        returns:
        Liquidity = 1 if bullish liquidity, -1 if bearish liquidity
        Level = the level of the liquidity
        End = the index of the last liquidity level
        Swept = the index of the candle that swept the liquidity
        """

        swing_highs_lows = swing_highs_lows.copy()

        # subtract the highest high from the lowest low
        pip_range = (max(ohlc["high"]) - min(ohlc["low"])) * range_percent

        # go through all of the high level and if there are more than 1 within the pip range, then it is liquidity
        liquidity = np.zeros(len(ohlc), dtype=np.int32)
        liquidity_level = np.zeros(len(ohlc), dtype=np.float32)
        liquidity_end = np.zeros(len(ohlc), dtype=np.int32)
        liquidity_swept = np.zeros(len(ohlc), dtype=np.int32)

        for i in range(len(ohlc)):
            if swing_highs_lows["HighLow"][i] == 1:
                high_level = swing_highs_lows["Level"][i]
                range_low = high_level - pip_range
                range_high = high_level + pip_range
                temp_liquidity_level = [high_level]
                start = i
                end = i
                swept = 0
                for c in range(i + 1, len(ohlc)):
                    if (
                        swing_highs_lows["HighLow"][c] == 1
                        and range_low <= swing_highs_lows["Level"][c] <= range_high
                    ):
                        end = c
                        temp_liquidity_level.append(
                            swing_highs_lows["Level"][c])
                        swing_highs_lows.loc[c, "HighLow"] = 0
                    if ohlc["high"].iloc[c] >= range_high:
                        swept = c
                        break
                if len(temp_liquidity_level) > 1:
                    average_high = sum(temp_liquidity_level) / \
                        len(temp_liquidity_level)
                    liquidity[i] = 1
                    liquidity_level[i] = average_high
                    liquidity_end[i] = end
                    liquidity_swept[i] = swept

        # now do the same for the lows
        for i in range(len(ohlc)):
            if swing_highs_lows["HighLow"][i] == -1:
                low_level = swing_highs_lows["Level"][i]
                range_low = low_level - pip_range
                range_high = low_level + pip_range
                temp_liquidity_level = [low_level]
                start = i
                end = i
                swept = 0
                for c in range(i + 1, len(ohlc)):
                    if (
                        swing_highs_lows["HighLow"][c] == -1
                        and range_low <= swing_highs_lows["Level"][c] <= range_high
                    ):
                        end = c
                        temp_liquidity_level.append(
                            swing_highs_lows["Level"][c])
                        swing_highs_lows.loc[c, "HighLow"] = 0
                    if ohlc["low"].iloc[c] <= range_low:
                        swept = c
                        break
                if len(temp_liquidity_level) > 1:
                    average_low = sum(temp_liquidity_level) / \
                        len(temp_liquidity_level)
                    liquidity[i] = -1
                    liquidity_level[i] = average_low
                    liquidity_end[i] = end
                    liquidity_swept[i] = swept

        liquidity = np.where(liquidity != 0, liquidity, np.nan)
        liquidity_level = np.where(
            ~np.isnan(liquidity), liquidity_level, np.nan)
        liquidity_end = np.where(~np.isnan(liquidity), liquidity_end, np.nan)
        liquidity_swept = np.where(
            ~np.isnan(liquidity), liquidity_swept, np.nan)

        liquidity = pd.Series(liquidity, name="Liquidity")
        level = pd.Series(liquidity_level, name="Level")
        liquidity_end = pd.Series(liquidity_end, name="End")
        liquidity_swept = pd.Series(liquidity_swept, name="Swept")

        return pd.concat([liquidity, level, liquidity_end, liquidity_swept], axis=1)

    @classmethod
    def previous_high_low(cls, ohlc: DataFrame, time_frame: str = "1D") -> Series:
        """
        Previous High Low
        This method returns the previous high and low of the given time frame.

        parameters:
        time_frame: str - the time frame to get the previous high and low 15m, 1H, 4H, 1D, 1W, 1M

        returns:
        PreviousHigh = the previous high
        PreviousLow = the previous low
        """

        ohlc.index = pd.to_datetime(ohlc.index)

        previous_high = np.zeros(len(ohlc), dtype=np.float32)
        previous_low = np.zeros(len(ohlc), dtype=np.float32)
        broken_high = np.zeros(len(ohlc), dtype=np.int32)
        broken_low = np.zeros(len(ohlc), dtype=np.int32)

        resampled_ohlc = ohlc.resample(time_frame).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        ).dropna()

        currently_broken_high = False
        currently_broken_low = False
        last_broken_time = None
        for i in range(len(ohlc)):
            resampled_previous_index = np.where(
                resampled_ohlc.index < ohlc.index[i]
            )[0]
            if len(resampled_previous_index) <= 1:
                previous_high[i] = np.nan
                previous_low[i] = np.nan
                continue
            resampled_previous_index = resampled_previous_index[-2]

            if last_broken_time != resampled_previous_index:
                currently_broken_high = False
                currently_broken_low = False
                last_broken_time = resampled_previous_index

            previous_high[i] = resampled_ohlc["high"].iloc[resampled_previous_index]
            previous_low[i] = resampled_ohlc["low"].iloc[resampled_previous_index]
            currently_broken_high = ohlc["high"].iloc[i] > previous_high[i] or currently_broken_high
            currently_broken_low = ohlc["low"].iloc[i] < previous_low[i] or currently_broken_low
            broken_high[i] = 1 if currently_broken_high else 0
            broken_low[i] = 1 if currently_broken_low else 0

        previous_high = pd.Series(previous_high, name="PreviousHigh")
        previous_low = pd.Series(previous_low, name="PreviousLow")
        broken_high = pd.Series(broken_high, name="BrokenHigh")
        broken_low = pd.Series(broken_low, name="BrokenLow")

        return pd.concat([previous_high, previous_low, broken_high, broken_low], axis=1)

    @classmethod
    def sessions(
        cls,
        ohlc: DataFrame,
        session: str,
        start_time: str = "",
        end_time: str = "",
        time_zone: str = "UTC",
    ) -> Series:
        """
        Sessions
        This method returns wwhich candles are within the session specified

        parameters:
        session: str - the session you want to check (Sydney, Tokyo, London, New York, Asian kill zone, London open kill zone, New York kill zone, london close kill zone, Custom)
        start_time: str - the start time of the session in the format "HH:MM" only required for custom session.
        end_time: str - the end time of the session in the format "HH:MM" only required for custom session.
        time_zone: str - the time zone of the candles can be in the format "UTC+0" or "GMT+0"

        returns:
        Active = 1 if the candle is within the session, 0 if not
        High = the highest point of the session
        Low = the lowest point of the session
        """

        if session == "Custom" and (start_time == "" or end_time == ""):
            raise ValueError("Custom session requires a start and end time")

        default_sessions = {
            "Sydney": {
                "start": "21:00",
                "end": "06:00",
            },
            "Tokyo": {
                "start": "00:00",
                "end": "09:00",
            },
            "London": {
                "start": "07:00",
                "end": "16:00",
            },
            "New York": {
                "start": "13:00",
                "end": "22:00",
            },
            "Asian kill zone": {
                "start": "00:00",
                "end": "04:00",
            },
            "London open kill zone": {
                "start": "6:00",
                "end": "9:00",
            },
            "New York kill zone": {
                "start": "11:00",
                "end": "14:00",
            },
            "london close kill zone": {
                "start": "14:00",
                "end": "16:00",
            },
            "Custom": {
                "start": start_time,
                "end": end_time,
            },
        }

        ohlc.index = pd.to_datetime(ohlc.index)
        if time_zone != "UTC":
            time_zone = time_zone.replace("GMT", "Etc/GMT")
            time_zone = time_zone.replace("UTC", "Etc/GMT")
            ohlc.index = ohlc.index.tz_localize(time_zone).tz_convert("UTC")

        start_time = datetime.strptime(
            default_sessions[session]["start"], "%H:%M"
        ).strftime("%H:%M")
        start_time = datetime.strptime(start_time, "%H:%M")
        end_time = datetime.strptime(
            default_sessions[session]["end"], "%H:%M"
        ).strftime("%H:%M")
        end_time = datetime.strptime(end_time, "%H:%M")

        # if the candles are between the start and end time then it is an active session
        active = np.zeros(len(ohlc), dtype=np.int32)
        high = np.zeros(len(ohlc), dtype=np.float32)
        low = np.zeros(len(ohlc), dtype=np.float32)

        for i in range(len(ohlc)):
            current_time = ohlc.index[i].strftime("%H:%M")
            # convert current time to the second of the day
            current_time = datetime.strptime(current_time, "%H:%M")
            if (start_time < end_time and start_time <= current_time <= end_time) or (
                start_time >= end_time
                and (start_time <= current_time or current_time <= end_time)
            ):
                active[i] = 1
                high[i] = max(ohlc["high"].iloc[i],
                              high[i - 1] if i > 0 else 0)
                low[i] = min(
                    ohlc["low"].iloc[i],
                    low[i - 1] if i > 0 and low[i - 1] != 0 else float("inf"),
                )

        active = pd.Series(active, name="Active")
        high = pd.Series(high, name="High")
        low = pd.Series(low, name="Low")

        return pd.concat([active, high, low], axis=1)

    @classmethod
    def retracements(cls, ohlc: DataFrame, swing_highs_lows: DataFrame) -> Series:
        """
        Retracement
        This method returns the percentage of a retracement from the swing high or low

        parameters:
        swing_highs_lows: DataFrame - provide the dataframe from the swing_highs_lows function

        returns:
        Direction = 1 if bullish retracement, -1 if bearish retracement
        CurrentRetracement% = the current retracement percentage from the swing high or low
        DeepestRetracement% = the deepest retracement percentage from the swing high or low
        """

        swing_highs_lows = swing_highs_lows.copy()

        direction = np.zeros(len(ohlc), dtype=np.int32)
        current_retracement = np.zeros(len(ohlc), dtype=np.float64)
        deepest_retracement = np.zeros(len(ohlc), dtype=np.float64)

        top = 0
        bottom = 0
        for i in range(len(ohlc)):
            if swing_highs_lows["HighLow"][i] == 1:
                direction[i] = 1
                top = swing_highs_lows["Level"][i]
                # deepest_retracement[i] = 0
            elif swing_highs_lows["HighLow"][i] == -1:
                direction[i] = -1
                bottom = swing_highs_lows["Level"][i]
                # deepest_retracement[i] = 0
            else:
                direction[i] = direction[i - 1] if i > 0 else 0

            if direction[i - 1] == 1:
                current_retracement[i] = round(
                    100 - (((ohlc["low"].iloc[i] - bottom) /
                           (top - bottom)) * 100), 1
                )
                deepest_retracement[i] = max(
                    (
                        deepest_retracement[i - 1]
                        if i > 0 and direction[i - 1] == 1
                        else 0
                    ),
                    current_retracement[i],
                )
            if direction[i] == -1:
                current_retracement[i] = round(
                    100 - ((ohlc["high"].iloc[i] - top) /
                           (bottom - top)) * 100, 1
                )
                deepest_retracement[i] = max(
                    (
                        deepest_retracement[i - 1]
                        if i > 0 and direction[i - 1] == -1
                        else 0
                    ),
                    current_retracement[i],
                )

        # shift the arrays by 1
        current_retracement = np.roll(current_retracement, 1)
        deepest_retracement = np.roll(deepest_retracement, 1)
        direction = np.roll(direction, 1)

        # remove the first 3 retracements as they get calculated incorrectly due to not enough data
        remove_first_count = 0
        for i in range(len(direction)):
            if i + 1 == len(direction):
                break
            if direction[i] != direction[i + 1]:
                remove_first_count += 1
            direction[i] = 0
            current_retracement[i] = 0
            deepest_retracement[i] = 0
            if remove_first_count == 3:
                direction[i + 1] = 0
                current_retracement[i + 1] = 0
                deepest_retracement[i + 1] = 0
                break

        direction = pd.Series(direction, name="Direction")
        current_retracement = pd.Series(
            current_retracement, name="CurrentRetracement%")
        deepest_retracement = pd.Series(
            deepest_retracement, name="DeepestRetracement%")

        return pd.concat([direction, current_retracement, deepest_retracement], axis=1)
