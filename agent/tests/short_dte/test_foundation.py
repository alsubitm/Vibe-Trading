from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.governance.ledger import verify_chain
from src.short_dte.config.loader import config_hash, load_config
from src.short_dte.config.models import ShortDTEConfig
from src.short_dte.data.clock import MarketSession, dte_bucket, market_clock, require_fresh
from src.short_dte.domain.enums import DTEBucket, SessionPhase
from src.short_dte.domain.market import UnderlyingBar, UnderlyingQuote
from src.short_dte.domain.options import OptionChainSnapshot, OptionContract, OptionQuote
from src.short_dte.observability.audit import make_event, write_audit
from src.short_dte.observability.logging import log_event
from src.short_dte.domain.portfolio import OptionPosition, PortfolioState
from src.short_dte.domain.orders import OrderRequest
from src.short_dte.domain.signals import ExitPlan, OptionLeg, TradeCandidate

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 15, tzinfo=UTC)


def contract(**overrides):
    data = dict(underlying="SPY", option_symbol="SPY-test-call", expiration=date(2026, 11, 2),
                strike="600", right="CALL", multiplier=100, settlement_type="PHYSICAL",
                exercise_style="AMERICAN")
    return OptionContract(**(data | overrides))


def quote(**overrides):
    return UnderlyingQuote(**(dict(symbol="SPY", timestamp=NOW, bid="600.01",
                                  ask="600.02", source="fixture") | overrides))


def option_quote(**overrides):
    return OptionQuote(**(dict(contract=contract(), timestamp=NOW, bid="1.00", ask="1.10",
                              underlying_price="600", source="fixture") | overrides))


def session(day=date(2026, 11, 2), opening=14, closing=21):
    return MarketSession(session_date=day,
                         opens_at=datetime.combine(day, datetime.min.time(), UTC)
                         + timedelta(hours=opening, minutes=30),
                         closes_at=datetime.combine(day, datetime.min.time(), UTC)
                         + timedelta(hours=closing))


def test_utc_normalization_and_decimal_roundtrip():
    value = quote(timestamp=datetime(2026, 11, 2, 18, tzinfo=timezone(timedelta(hours=3))))
    assert value.timestamp == NOW and value.timestamp.tzinfo == UTC
    assert value.bid == Decimal("600.01")
    assert UnderlyingQuote.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("changes", [
    {"timestamp": datetime(2026, 11, 2)}, {"bid": "0"}, {"bid": "601"},
    {"bid": "NaN"}, {"bid_size": -1}, {"bid_size": True}, {"unexpected": 1},
])
def test_invalid_quotes_fail(changes):
    with pytest.raises(ValidationError):
        quote(**changes)


def test_contract_identity_is_frozen_and_sizes_positive():
    with pytest.raises(ValidationError):
        contract().strike = Decimal("1")
    with pytest.raises(ValidationError):
        contract(multiplier=0)


def test_bar_invariants():
    bar = UnderlyingBar(symbol="SPY", timeframe="1m", timestamp=NOW, open="600", high="601",
                        low="599", close="600", volume=0, source="fixture")
    assert UnderlyingBar.model_validate_json(bar.model_dump_json()) == bar
    with pytest.raises(ValidationError):
        UnderlyingBar(symbol="SPY", timeframe="1m", timestamp=NOW, open="600", high="601",
                      low="599", close="602", volume=0, source="fixture")


@pytest.mark.parametrize("changes", [{"ask": "0.5"}, {"delta": 2}, {"gamma": -1},
                                      {"theta": float("nan")}, {"implied_volatility": -0.1}])
def test_invalid_option_quotes_fail(changes):
    with pytest.raises(ValidationError):
        option_quote(**changes)


def test_chain_integrity_and_immutable_children():
    data = dict(underlying="SPY", timestamp=NOW, underlying_price="600",
                expiration=date(2026, 11, 2), source="fixture", is_complete=True,
                snapshot_kind="COMPLETE", contracts=[option_quote()])
    chain = OptionChainSnapshot(**data)
    assert isinstance(chain.contracts, tuple)
    for changes in ({"contracts": []}, {"contracts": [option_quote(), option_quote()]},
                    {"underlying": "QQQ"}, {"snapshot_kind": "PARTIAL"},
                    {"contracts": [option_quote(timestamp=NOW + timedelta(seconds=1))]}):
        with pytest.raises(ValidationError):
            OptionChainSnapshot(**(data | changes))


@pytest.mark.parametrize("hour,minute,phase", [
    (8, 59, "CLOSED"), (9, 0, "PREMARKET"), (14, 30, "OPENING"),
    (15, 0, "MORNING"), (16, 30, "MIDDAY"), (18, 30, "AFTERNOON"),
    (20, 0, "POWER_HOUR"), (21, 0, "POSTMARKET"),
])
def test_session_boundaries(hour, minute, phase):
    clock = market_clock(NOW.replace(hour=hour, minute=minute), session())
    assert clock.session_phase.value == phase
    assert clock.timestamp_utc.tzinfo == UTC


def test_missing_holiday_weekend_session_is_closed():
    for day in (date(2026, 11, 2), date(2026, 11, 7), date(2026, 12, 25)):
        assert market_clock(datetime.combine(day, datetime.min.time(), UTC)).session_phase == SessionPhase.CLOSED


def test_dst_and_early_close():
    summer = date(2026, 7, 2)
    assert market_clock(datetime(2026, 7, 2, 13, 30, tzinfo=UTC),
                        session(summer, 13, 20)).session_phase == SessionPhase.OPENING
    early = date(2026, 11, 27)
    assert market_clock(datetime(2026, 11, 27, 17, 0, tzinfo=UTC),
                        session(early, 14, 18)).session_phase == SessionPhase.POWER_HOUR
    assert market_clock(datetime(2026, 11, 27, 18, 0, tzinfo=UTC),
                        session(early, 14, 18)).session_phase == SessionPhase.POSTMARKET


def test_dte_uses_exchange_date_not_utc_date():
    assert dte_bucket(date(2026, 11, 2), datetime(2026, 11, 3, 1, tzinfo=UTC)) == DTEBucket.ZERO_DTE
    for days, expected in ((1, DTEBucket.ONE_DTE), (3, DTEBucket.TWO_THREE_DTE), (7, DTEBucket.FOUR_SEVEN_DTE)):
        assert dte_bucket(NOW.date() + timedelta(days=days), NOW) == expected
    for days in (-1, 8):
        with pytest.raises(ValueError):
            dte_bucket(NOW.date() + timedelta(days=days), NOW)


def test_freshness_boundaries_and_future_data():
    require_fresh(NOW - timedelta(seconds=3), NOW, 3)
    for timestamp in (NOW - timedelta(seconds=3.01), NOW + timedelta(microseconds=1)):
        with pytest.raises(ValueError):
            require_fresh(timestamp, NOW, 3)
    with pytest.raises(ValueError):
        require_fresh(datetime(2026, 11, 2), NOW, 3)


def test_config_hash_is_semantic_and_nested_config_is_frozen(tmp_path):
    a = tmp_path / "a.yaml"
    a.write_text("schema_version: 1\nmode: research\n", encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text("# comment\nmode: research\nschema_version: 1\n", encoding="utf-8")
    config = load_config()
    assert config_hash(load_config(a)) == config_hash(load_config(b)) == config_hash(config)
    assert isinstance(config.universe, tuple)
    with pytest.raises(ValidationError):
        config.freshness.option_quote_seconds = 8
    a.write_text("schema_version: 1\nmode: paper\n", encoding="utf-8")
    assert config_hash(load_config(a)) != config_hash(config)


@pytest.mark.parametrize("raw", ["", "[]", "mode: research", "schema_version: 2",
    "schema_version: true", "schema_version: 1\nschema_version: 1",
    "schema_version: 1\nlive_trading_enabled: true", "schema_version: 1\nmode: live",
    "schema_version: 1\ncredentials: secret", "schema_version: 1\nuniverse: []",
    "schema_version: 1\nfreshness: {option_quote_seconds: -1}",
    "schema_version: 1\nbuckets: {zero_dte: {minimum_score: .nan}}"])
def test_invalid_config_fails(tmp_path, raw):
    path = tmp_path / "bad.yaml"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        load_config(path)


def test_research_audit_reuses_chain_and_redaction(tmp_path):
    config = load_config()
    event = make_event(config=config, event_id=UUID(int=1), timestamp=NOW,
                       run_id="fixture-run", event_type="config.loaded")
    path = tmp_path / "research.jsonl"
    write_audit(path, event, {"api_key": "test-secret", "nested": {"access_token": "secret"}})
    write_audit(path, event)
    raw = path.read_text(encoding="utf-8")
    assert "test-secret" not in raw and '"secret"' not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]["config_hash"] == config_hash(config)
    assert rows[1]["seq"] == 2
    assert verify_chain(path).ok


def test_audit_io_failure_propagates(tmp_path):
    event = make_event(config=load_config(), event_id=UUID(int=1), timestamp=NOW,
                       run_id="test", event_type="data.invalid", decision="NO_NEW_TRADES")
    with pytest.raises(OSError):
        write_audit(tmp_path, event)


def test_structured_logging_has_hash_and_redaction(caplog):
    event = make_event(config=load_config(), event_id=UUID(int=1), timestamp=NOW,
                       run_id="test", event_type="data.invalid")
    with caplog.at_level(logging.INFO):
        log_event(logging.getLogger("short_dte.test"), event, {"api_key": "test-secret"})
    payload = json.loads(caplog.records[-1].getMessage())
    assert payload["config_hash"] == event.config_hash
    assert "test-secret" not in caplog.text


def test_order_candidate_portfolio_roundtrips_and_validation():
    leg = OptionLeg(contract=contract(), side="BUY", quantity=1, expected_price="1",
                    delta=0.5, gamma=0.1, theta=-0.2, vega=0.1)
    exit_plan = ExitPlan(time_stop=NOW + timedelta(hours=1), forced_exit=NOW + timedelta(hours=2),
                         profit_target_pct_max_profit=50, stop_loss_pct_max_loss=50)
    data = dict(candidate_id=UUID(int=1), timestamp=NOW, underlying="SPY", strategy_id="fixture",
                legs=[leg], net_debit_credit="1", max_profit=None, max_loss="100",
                position_delta=0.5, position_gamma=0.1, position_theta=-0.2, position_vega=0.1,
                liquidity_score=80, volatility_score=80, regime_score=80, structure_score=80,
                risk_reward_score=80, total_score=80, exit_plan=exit_plan)
    candidate = TradeCandidate(**data)
    assert TradeCandidate.model_validate_json(candidate.model_dump_json()) == candidate
    for changes in ({"legs": []}, {"max_loss": None}, {"max_loss": "-1"},
                    {"total_score": 101}, {"probability_estimates": {"profit": 1.1}},
                    {"exit_plan": ExitPlan(forced_exit=NOW)},
                    {"exit_plan": exit_plan.model_copy(update={"time_stop": NOW})}):
        with pytest.raises(ValidationError):
            TradeCandidate(**(data | changes))
    order = OrderRequest(order_id=UUID(int=2), candidate_id=candidate.candidate_id,
                         timestamp=NOW, legs=[leg], limit_price="1", price_effect="DEBIT")
    assert OrderRequest.model_validate_json(order.model_dump_json()) == order
    with pytest.raises(ValidationError):
        OrderRequest(**(order.model_dump() | {"legs": []}))
    position = OptionPosition(position_id=UUID(int=3), contract=contract(), side="LONG",
                              quantity=1, average_price="1")
    portfolio = PortfolioState(timestamp=NOW, account_ref="paper", equity="1000", cash="900",
                               positions=[position])
    assert PortfolioState.model_validate_json(portfolio.model_dump_json()) == portfolio
    with pytest.raises(ValidationError):
        OptionLeg(**(leg.model_dump() | {"quantity": 0}))


def test_exit_plan_matches_spec_and_roundtrips():
    plan = ExitPlan(forced_exit=NOW + timedelta(hours=2),
                    underlying_invalidation_level="590", trailing_logic={"enabled": False})
    assert plan.time_stop is None
    assert ExitPlan.model_validate_json(plan.model_dump_json()) == plan


@pytest.mark.parametrize("changes", [
    {"forced_exit": datetime(2026, 11, 2)},
    {"time_stop": datetime(2026, 11, 2)},
    {"time_stop": NOW + timedelta(hours=3)},
    {"profit_target_pct_max_profit": 101}, {"stop_loss_pct_max_loss": -1},
    {"underlying_invalidation_level": "0"},
])
def test_invalid_exit_plan_fails(changes):
    with pytest.raises(ValidationError):
        ExitPlan(**({"forced_exit": NOW + timedelta(hours=2)} | changes))


def test_forced_exit_is_required():
    with pytest.raises(ValidationError):
        ExitPlan(time_stop=NOW + timedelta(hours=1))


@pytest.mark.parametrize("changes", [
    {"schema_version": True}, {"schema_version": 1.0},
    {"live_trading_enabled": 0},
])
def test_direct_config_construction_rejects_coerced_safety_fields(changes):
    with pytest.raises(ValidationError):
        ShortDTEConfig(**({"schema_version": 1} | changes))


def test_audit_redacts_tuple_details_before_serialization(tmp_path, caplog):
    event = make_event(config=load_config(), event_id=UUID(int=1), timestamp=NOW,
                       run_id="test", event_type="fixture")
    details = {"items": ({"api_key": "tuple-fixture-secret"},)}
    path = tmp_path / "research.jsonl"
    write_audit(path, event, details)
    with caplog.at_level(logging.INFO):
        log_event(logging.getLogger("short_dte.test"), event, details)
    assert "tuple-fixture-secret" not in path.read_text(encoding="utf-8")
    assert "tuple-fixture-secret" not in caplog.text
    assert details["items"][0]["api_key"] == "tuple-fixture-secret"


@pytest.mark.parametrize("changes", [{"delta": 2}, {"gamma": -1}, {"vega": -1}])
def test_leg_greeks_use_quote_constraints(changes):
    with pytest.raises(ValidationError):
        OptionLeg(**(dict(contract=contract(), side="BUY", quantity=1, expected_price="1",
                          delta=0.5, gamma=0.1, theta=-0.2, vega=0.1) | changes))
