from src.data_client.tushare import FIELDS


def test_tushare_exposes_bounded_fields_for_local_financial_reports():
    assert FIELDS["income"] == (
        "ts_code,ann_date,f_ann_date,end_date,report_type,basic_eps,total_revenue,"
        "revenue,operate_profit,total_profit,n_income,n_income_attr_p"
    )
    assert FIELDS["balancesheet"] == (
        "ts_code,ann_date,f_ann_date,end_date,report_type,total_cur_assets,"
        "total_assets,total_cur_liab,total_liab,total_hldr_eqy_exc_min_int,money_cap"
    )
    assert FIELDS["cashflow"] == (
        "ts_code,ann_date,f_ann_date,end_date,report_type,n_cashflow_act,"
        "n_cashflow_inv_act,n_cash_flows_fnc_act,c_cash_equ_end_period"
    )
    assert FIELDS["fina_indicator"] == (
        "ts_code,ann_date,end_date,eps,profit_dedt,gross_margin,current_ratio,"
        "quick_ratio,debt_to_assets,roe,roa,ocfps"
    )
    assert FIELDS["disclosure_date"] == (
        "ts_code,ann_date,end_date,pre_date,actual_date,modify_date"
    )
