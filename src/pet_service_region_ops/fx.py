"""汇率版本管理与跨币种折算。

业务规则（对应需求）：
- 每条收入/退款/成本/义务记录都携带使用的 ``fx_version``，折算结果可追溯；
- 已结账期（final）按"当时汇率"（as-booked）折算，汇率修订不重写历史；
- 未结账期可以用当前最新汇率重估，重估差额单独列示，不覆盖原值；
- 未结算渠道金额保留本币，禁止与美元净收入直接相加。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import FxRate, _q2


class FxError(ValueError):
    """汇率查找或折算错误。"""


@dataclass(frozen=True)
class ConvertedAmount:
    """一次折算的完整轨迹。"""
    local_amount: Decimal
    currency: str
    fx_version: str
    rate_to_usd: Decimal
    usd_amount: Decimal


class FxLedger:
    """按地区/版本索引的汇率台账。"""

    def __init__(self, fx_rates: list[FxRate]):
        self._rates: dict[tuple[str, str], FxRate] = {
            (r.region, r.fx_version): r for r in fx_rates
        }
        # 每个地区的当前有效版本（valid_to 为空）
        self._current: dict[str, FxRate] = {}
        for rate in fx_rates:
            if rate.valid_to == "":
                existing = self._current.get(rate.region)
                if existing is not None:
                    raise FxError(
                        f"地区 {rate.region} 存在两个当前有效汇率版本: "
                        f"{existing.fx_version} 与 {rate.fx_version}"
                    )
                self._current[rate.region] = rate

    # ------------------------------------------------------------------
    def rate(self, region: str, fx_version: str) -> FxRate:
        try:
            return self._rates[(region, fx_version)]
        except KeyError:
            raise FxError(
                f"找不到地区 {region} 的汇率版本 {fx_version}"
            ) from None

    def current_rate(self, region: str) -> FxRate:
        try:
            return self._current[region]
        except KeyError:
            raise FxError(f"地区 {region} 没有当前有效汇率版本") from None

    def versions(self, region: str) -> list[FxRate]:
        return sorted(
            (r for r in self._rates.values() if r.region == region),
            key=lambda r: r.valid_from,
        )

    # ------------------------------------------------------------------
    def convert(
        self,
        local_amount: Decimal,
        region: str,
        fx_version: str,
    ) -> ConvertedAmount:
        """按指定历史版本折算（as-booked 口径）。"""
        rate = self.rate(region, fx_version)
        usd = _q2(local_amount * rate.rate_to_usd)
        return ConvertedAmount(
            local_amount=local_amount,
            currency=rate.currency,
            fx_version=fx_version,
            rate_to_usd=rate.rate_to_usd,
            usd_amount=usd,
        )

    def restate_at_current(
        self,
        local_amount: Decimal,
        region: str,
        booked_version: str,
    ) -> tuple[ConvertedAmount, ConvertedAmount, Decimal]:
        """用当前汇率重估未结账期金额。

        返回 (按记账版本折算, 按当前版本折算, 重估差额)。
        差额为正表示当前汇率下收入上修；历史折算值保持不变。
        """
        booked = self.convert(local_amount, region, booked_version)
        current_rate = self.current_rate(region)
        restated = self.convert(local_amount, region, current_rate.fx_version)
        delta = _q2(restated.usd_amount - booked.usd_amount)
        return booked, restated, delta
