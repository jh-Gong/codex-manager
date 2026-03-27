/**
 * 支付页面 JavaScript
 */

let selectedPlan = 'plus';
let generatedLink = '';
let countryCurrencyMap = {};  // 动态从接口加载

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadAccounts();
    loadCountries();
});

// 加载国家/货币列表
async function loadCountries() {
    const sel = document.getElementById('country-select');
    try {
        const resp = await fetch('/api/payment/countries');
        const data = await resp.json();
        const countries = data.countries || [];

        // 重建映射表
        countryCurrencyMap = {};
        countries.forEach(c => {
            countryCurrencyMap[c.country_code] = c.currency;
        });

        // 记住当前选中值
        const current = sel.value;

        // 渲染选项
        sel.innerHTML = countries.map(c =>
            `<option value="${c.country_code}">${c.country_name} (${c.currency})</option>`
        ).join('');

        // 恢复选中或默认 SG
        sel.value = current && countryCurrencyMap[current] ? current : 'SG';
        onCountryChange();

        if (!data.success) {
            console.warn('国家列表使用内置 fallback:', data.error);
        }
    } catch (e) {
        console.error('加载国家列表失败:', e);
        sel.innerHTML = '<option value="SG">Singapore (SGD)</option>';
        countryCurrencyMap = { SG: 'SGD' };
        onCountryChange();
    }
}

// 加载账号列表
async function loadAccounts() {
    try {
        const resp = await fetch('/api/accounts?page=1&page_size=100&status=active');
        const data = await resp.json();
        const sel = document.getElementById('account-select');
        sel.innerHTML = '<option value="">-- 请选择账号 --</option>';
        (data.accounts || []).forEach(acc => {
            const opt = document.createElement('option');
            opt.value = acc.id;
            opt.textContent = acc.email;
            sel.appendChild(opt);
        });
    } catch (e) {
        console.error('加载账号失败:', e);
    }
}

// 国家切换
function onCountryChange() {
    const country = document.getElementById('country-select').value;
    const currency = countryCurrencyMap[country] || '';
    document.getElementById('currency-display').value = currency;
}

// 选择套餐
function selectPlan(plan) {
    selectedPlan = plan;
    document.getElementById('plan-plus').classList.toggle('selected', plan === 'plus');
    document.getElementById('plan-team').classList.toggle('selected', plan === 'team');
    document.getElementById('team-options').classList.toggle('show', plan === 'team');
    // 隐藏已生成的链接
    document.getElementById('link-box').classList.remove('show');
    generatedLink = '';
}

// 生成支付链接
async function generateLink() {
    const accountId = document.getElementById('account-select').value;
    if (!accountId) {
        ui.showToast('请先选择账号', 'warning');
        return;
    }

    const country = document.getElementById('country-select').value || 'SG';

    const currency = countryCurrencyMap[country] || '';
    const body = {
        account_id: parseInt(accountId),
        plan_type: selectedPlan,
        country: country,
        currency: currency,
    };

    if (selectedPlan === 'team') {
        body.workspace_name = document.getElementById('workspace-name').value || 'MyTeam';
        body.seat_quantity = parseInt(document.getElementById('seat-quantity').value) || 5;
        body.price_interval = document.getElementById('price-interval').value;
    }

    const btn = document.querySelector('.form-actions .btn-primary');
    if (btn) { btn.disabled = true; btn.textContent = '生成中...'; }

    try {
        const resp = await fetch('/api/payment/generate-link', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.success && data.link) {
            generatedLink = data.link;
            document.getElementById('link-text').value = data.link;
            document.getElementById('link-box').classList.add('show');
            document.getElementById('open-status').textContent = '';
            ui.showToast('支付链接生成成功', 'success');
        } else {
            ui.showToast(data.detail || '生成链接失败', 'error');
        }
    } catch (e) {
        ui.showToast('请求失败: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '生成支付链接'; }
    }
}

// 复制链接
function copyLink() {
    if (!generatedLink) return;
    navigator.clipboard.writeText(generatedLink).then(() => {
        ui.showToast('已复制到剪贴板', 'success');
    }).catch(() => {
        const ta = document.getElementById('link-text');
        ta.select();
        document.execCommand('copy');
        ui.showToast('已复制到剪贴板', 'success');
    });
}

// 无痕打开浏览器（携带账号 cookie）
async function openIncognito() {
    if (!generatedLink) {
        ui.showToast('请先生成链接', 'warning');
        return;
    }
    const accountId = document.getElementById('account-select').value;
    const statusEl = document.getElementById('open-status');
    statusEl.textContent = '正在打开...';
    try {
        const body = { url: generatedLink };
        if (accountId) body.account_id = parseInt(accountId);

        const resp = await fetch('/api/payment/open-incognito', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = '已在无痕模式打开浏览器';
            ui.showToast('无痕浏览器已打开', 'success');
        } else {
            statusEl.textContent = data.message || '未找到可用浏览器，请手动复制链接';
            ui.showToast(data.message || '未找到浏览器', 'warning');
        }
    } catch (e) {
        statusEl.textContent = '请求失败: ' + e.message;
        ui.showToast('请求失败', 'error');
    }
}
