FF_MIN = 0.72

# 油样台账到期日与报送拦截共用同一份规则，报送口和台账专页都调它，
# 不会出现“表上显示过期、报送却放行”的两张皮。
def oil_block_reason(expiry_date, today) -> str | None:
    if expiry_date < today:
        return f"油样已于 {expiry_date.isoformat()} 过期，请重新化验合格后再报送"
    return None


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor} 低于 {FF_MIN}"
