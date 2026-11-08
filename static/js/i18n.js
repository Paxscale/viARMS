/**
 * viARMS i18n — Multi-Language Translation System
 * Supports: English (en), Nigerian Pidgin (pidgin), Yorùbá (yo), Igbo (ig), Hausa (ha)
 *
 * Usage:
 *   - Add data-i18n="key" to any HTML element to auto-translate its textContent.
 *   - Call viARMSi18n.setLang('yo') to switch languages.
 *   - Language preference is stored in localStorage.
 */

const viARMSi18n = (() => {
    const translations = {
        en: {
            // Auth / Login
            login_subtitle: 'Sales & Records Manager Login',
            username: 'Username',
            password: 'Password',
            sign_in: 'Sign In',
            no_account: "Don't have an account?",
            create_account: 'Create Free Account',
            sign_out: 'Sign Out',

            // Registration Page
            register_title: 'Create your store owner account',
            label_username: 'Username',
            label_email: 'Email Address',
            label_password: 'Password',
            label_confirm_password: 'Confirm Password',
            hdr_business_details: 'Business Setup',
            label_business_name: 'Business/Shop Name',
            label_business_type: 'Business Type',
            opt_general: 'General Retail / Provision',
            opt_pharma: 'Pharmaceuticals / Pharmacy',
            opt_book_accessories: 'Bookshop & Accessories',
            opt_utensils: 'Utensils & Housewares',
            hdr_pricing_plan: 'Choose Pricing Plan',
            plan_free_name: 'Free Plan',
            plan_free_desc: '1 Shop, 1 Manager, 1 Cashier',
            plan_premium_name: 'Premium Plan',
            plan_premium_desc: 'Unlimited Shops, Managers & Cashiers',
            plan_golden_name: 'Golden Premium',
            plan_golden_desc: 'Everything + Advisor & Analysis',
            approval_notice: 'Note: All registered accounts must be approved by the system administrator before logging in.',
            btn_register: 'Sign Up',
            login_prompt: 'Already have an account?',
            btn_login_link: 'Sign In',

            // Sidebar Navigation
            nav_dashboard: 'Dashboard',
            nav_pos: 'POS Checkout',
            nav_products: 'Products & Stock',
            nav_sales: 'Sales Log',
            nav_staff: 'Staff Directory',
            nav_shops: 'Shop Branches',
            nav_settings: 'Store Settings',
            nav_admin_panel: 'Admin Panel',

            // POS
            pos_search: 'Search or scan product...',
            pos_all: 'All',
            pos_checkout: 'Checkout',
            pos_cart_empty: 'Cart is empty',
            pos_total: 'Total',
            pos_pay: 'Process Payment',

            // Dashboard
            dash_total_sales: "Today's Sales",
            dash_revenue: 'Revenue',
            dash_products: 'Total Products',
            dash_low_stock: 'Low Stock Alerts',

            // Status Banner
            banner_pending: 'Account Pending Approval. Your account is under review.',
            banner_free: 'Free Plan — 1 shop, 1 manager, 1 cashier limit.',
            banner_upgrade: 'Upgrade ↗',
        },

        pidgin: {
            login_subtitle: 'Enter Your Shop Account',
            username: 'Username',
            password: 'Password',
            sign_in: 'Shiga',
            no_account: 'You never get account?',
            create_account: 'Open Free Account',
            sign_out: 'Comot',

            register_title: 'Open your shop owner account',
            label_username: 'Username',
            label_email: 'Email',
            label_password: 'Password',
            label_confirm_password: 'Type Password Again',
            hdr_business_details: 'Business Details',
            label_business_name: 'Business/Shop Name',
            label_business_type: 'Wetin You Dey Sell?',
            opt_general: 'General Shop / Provision',
            opt_pharma: 'Chemist / Pharmacy',
            opt_book_accessories: 'Book Shop & Accessories',
            opt_utensils: 'Utensils & Housewares',
            hdr_pricing_plan: 'Pick Your Plan',
            plan_free_name: 'Free Plan',
            plan_free_desc: '1 Shop, 1 Manager, 1 Cashier',
            plan_premium_name: 'Premium Plan',
            plan_premium_desc: 'Everything Unlimited',
            plan_golden_name: 'Golden Premium',
            plan_golden_desc: 'Everything + Advisor & Analysis',
            approval_notice: 'Note: Admin go approve your account before you fit login.',
            btn_register: 'Sign Up',
            login_prompt: 'You don get account?',
            btn_login_link: 'Enter',

            nav_dashboard: 'Dashboard',
            nav_pos: 'Sell/Checkout',
            nav_products: 'Products & Stock',
            nav_sales: 'Sales Record',
            nav_staff: 'Workers List',
            nav_shops: 'Shop Branches',
            nav_settings: 'Settings',
            nav_admin_panel: 'Admin Panel',

            pos_search: 'Find or scan product...',
            pos_all: 'All',
            pos_checkout: 'Sell',
            pos_cart_empty: 'Cart empty',
            pos_total: 'Total',
            pos_pay: 'Collect Money',

            dash_total_sales: 'Today Sales',
            dash_revenue: 'Money Wey Enter',
            dash_products: 'All Products',
            dash_low_stock: 'Stock Dey Finish',

            banner_pending: 'Account never approve yet. Make you wait small.',
            banner_free: 'Free Plan — 1 shop, 1 manager, 1 cashier only.',
            banner_upgrade: 'Upgrade ↗',
        },

        yo: {
            login_subtitle: 'Wọlé sí Ètò Ọja',
            username: 'Orúkọ Olùmúlò',
            password: 'Ọ̀rọ̀ Àṣírí',
            sign_in: 'Wọlé',
            no_account: 'Ṣé o níní àkọọlẹ̀?',
            create_account: 'Ṣẹ̀dá Àkọọlẹ̀ Ọfẹ',
            sign_out: 'Jáde',

            register_title: 'Ṣẹ̀dá àkọọlẹ̀ oníṣòwò rẹ',
            label_username: 'Orúkọ Olùmúlò',
            label_email: 'Àdírẹ́sì Ímeèlì',
            label_password: 'Ọ̀rọ̀ Àṣírí',
            label_confirm_password: 'Fìdí Ọ̀rọ̀ Àṣírí Múlẹ̀',
            hdr_business_details: 'Ìtọ́kasí Òwò',
            label_business_name: 'Orúkọ Ọja/Òwò',
            label_business_type: 'Irú Òwò',
            opt_general: 'Ọja Àpapọ̀ / Provision',
            opt_pharma: 'Ilé Ìtajà Oògùn',
            opt_book_accessories: 'Ilé Ìwé àti Ohun Ọṣọ́',
            opt_utensils: 'Ohun Èlò Ilé àti Oúnjẹ',
            hdr_pricing_plan: 'Yan Ètò Owó',
            plan_free_name: 'Ètò Ọfẹ',
            plan_free_desc: '1 Ọja, 1 Olùdarí, 1 Akowe',
            plan_premium_name: 'Ètò Premium',
            plan_premium_desc: 'Láì ní ìdíwọ́',
            plan_golden_name: 'Golden Premium',
            plan_golden_desc: 'Gbogbo ẹ̀ + Olùdámọ̀ràn',
            approval_notice: 'Àkíyèsí: Alàkòso ní láti fọwọ́ sí àkọọlẹ̀ rẹ kí o tó lè wọlé.',
            btn_register: 'Ṣẹ̀dá Àkọọlẹ̀ Ọja',
            login_prompt: 'Ṣé o ti ní àkọọlẹ̀?',
            btn_login_link: 'Wọlé',

            nav_dashboard: 'Pàgọ́ Ìpínlẹ̀',
            nav_pos: 'Tà Ọjà',
            nav_products: 'Àwọn Ọjà',
            nav_sales: 'Ìgbàpadà Tita',
            nav_staff: 'Àwọn Òṣìṣẹ́',
            nav_shops: 'Àwọn Ẹ̀ka Ọja',
            nav_settings: 'Ìtọ́kasí',
            nav_admin_panel: 'Pánẹ́lì Alàkòso',

            pos_search: 'Wá tàbí ṣàyẹ̀wò ọjà...',
            pos_all: 'Gbogbo',
            pos_checkout: 'Sanwó',
            pos_cart_empty: 'Àpò ọjà ṣofo',
            pos_total: 'Àpapọ̀',
            pos_pay: 'Gbà Owó',

            dash_total_sales: 'Tita Òní',
            dash_revenue: 'Owó Tó Wọ',
            dash_products: 'Àwọn Ọjà',
            dash_low_stock: 'Ọjà Tó Fẹ́rẹ̀ Parí',

            banner_pending: 'Àkọọlẹ̀ ń dúró fún ìfọwọ́sí. Jọ̀wọ́ dúró.',
            banner_free: 'Ètò Ọfẹ — 1 ọja, 1 olùdarí, 1 akowe nikan.',
            banner_upgrade: 'Gba Upgrade ↗',
        },

        ig: {
            login_subtitle: 'Banye na Ngalaba Ahịa Gị',
            username: 'Aha Ojiji',
            password: 'Okwuntughe',
            sign_in: 'Banye',
            no_account: 'Ị nweghị akaụntụ?',
            create_account: 'Mepụta Akaụntụ Efu',
            sign_out: 'Pụọ',

            register_title: 'Mepụta akaụntụ onye nwe ụlọ ahịa gị',
            label_username: 'Aha Ojiji',
            label_email: 'Adreesị Ozi-e',
            label_password: 'Okwuntughe',
            label_confirm_password: 'Kwado Okwuntughe',
            hdr_business_details: 'Mmalite Azụmaahịa',
            label_business_name: 'Aha Ụlọ Ahịa',
            label_business_type: 'Ụdị Azụmaahịa',
            opt_general: 'Ụlọ Ahịa Izugbe / Provision',
            opt_pharma: 'Ụlọ Ọgwụ / Pharmacy',
            opt_book_accessories: 'Ụlọ Akwụkwọ na Ngwongwo',
            opt_utensils: 'Ngwa Ụlọ na Ngwa Nri',
            hdr_pricing_plan: 'Họrọ Atụmatụ Ọnụ Ahịa',
            plan_free_name: 'Atụmatụ Efu',
            plan_free_desc: '1 Ụlọ ahịa, 1 Onye njikwa, 1 Onye ọdịnaya',
            plan_premium_name: 'Atụmatụ Premium',
            plan_premium_desc: 'Ọ nweghị oke',
            plan_golden_name: 'Golden Premium',
            plan_golden_desc: 'Ihe Niile + Onye Nduzi',
            approval_notice: 'Mara: Onye nchịkwa ga-akwado akaụntụ gị tupu ị banye.',
            btn_register: 'Mepụta Akaụntụ',
            login_prompt: 'I nwere akaụntụ?',
            btn_login_link: 'Banye',

            nav_dashboard: 'Dashboard',
            nav_pos: 'Ire Ahịa',
            nav_products: 'Ngwongwo & Stock',
            nav_sales: 'Ndebere Ire',
            nav_staff: 'Ndepụta Ndị Ọrụ',
            nav_shops: 'Ngalaba Ụlọ Ahịa',
            nav_settings: 'Ntọala',
            nav_admin_panel: 'Pánẹ́lì Nchịkwa',

            pos_search: 'Chọọ ma ọ bụ nyochaa ngwongwo...',
            pos_all: 'Nile',
            pos_checkout: 'Kwụọ Ụgwọ',
            pos_cart_empty: 'Nkwụcha ọcha',
            pos_total: 'Mgbakọ',
            pos_pay: 'Natọ Ụgwọ',

            dash_total_sales: 'Ire Ụbọchị Taa',
            dash_revenue: 'Ego Abanyere',
            dash_products: 'Ngwongwo Nile',
            dash_low_stock: 'Stock na-emede',

            banner_pending: 'Akaụntụ na-atọ ule. Biko chere obere oge.',
            banner_free: 'Atụmatụ Efu — ụlọ ahịa 1, onye njikwa 1, onye ọdịnaya 1 naanị.',
            banner_upgrade: 'Wuo Elu ↗',
        },

        ha: {
            login_subtitle: 'Shiga Tsarin Kasuwancinku',
            username: 'Sunan Mai Amfani',
            password: 'Kalmar Sirri',
            sign_in: 'Shiga',
            no_account: 'Ba ku da asusu?',
            create_account: 'Buɗe Asusu Na Kyauta',
            sign_out: 'Fita',

            register_title: 'Buɗe asusun mai shago',
            label_username: 'Sunan Mai Amfani',
            label_email: 'Adireshin Imel',
            label_password: 'Kalmar Sirri',
            label_confirm_password: 'Tabbatar da Kalmar Sirri',
            hdr_business_details: 'Bayanan Kasuwanci',
            label_business_name: 'Sunan Shago/Kasuwanci',
            label_business_type: 'Nau\'in Kasuwanci',
            opt_general: 'Kantin Sayarwa / Provision',
            opt_pharma: 'Kantin Magani / Pharmacy',
            opt_book_accessories: 'Kantin Littattafai da Kayan Ado',
            opt_utensils: 'Kayan Gida da Kayan Abinci',
            hdr_pricing_plan: 'Zaɓi Shirin Farashi',
            plan_free_name: 'Shirin Kyauta',
            plan_free_desc: '1 Shago, 1 Manaja, 1 Ma\'aji',
            plan_premium_name: 'Shirin Premium',
            plan_premium_desc: 'Ba iyaka',
            plan_golden_name: 'Golden Premium',
            plan_golden_desc: 'Komai + Mai Bada Shawara',
            approval_notice: 'Lura: Dole mai gudanarwa ya amince da asusunka kafin ka shiga.',
            btn_register: 'Buɗe Asusun',
            login_prompt: 'Kuna da asusu?',
            btn_login_link: 'Shiga',

            nav_dashboard: 'Dashboard',
            nav_pos: 'Sayarwa',
            nav_products: 'Kayayyaki & Stock',
            nav_sales: 'Rikodin Sayarwa',
            nav_staff: 'Jerin Ma\'aikata',
            nav_shops: 'Rassan Shago',
            nav_settings: 'Saiti',
            nav_admin_panel: 'Shafin Gudanarwa',

            pos_search: 'Bincika ko sken kayayyaki...',
            pos_all: 'Duka',
            pos_checkout: 'Biya',
            pos_cart_empty: 'Kwandon sayayya yana komai',
            pos_total: 'Jimiliya',
            pos_pay: 'Karɓi Biyan Kuɗi',

            dash_total_sales: 'Sayarwa Ta Yau',
            dash_revenue: 'Kuɗin da ya shigo',
            dash_products: 'Dukkan Kayayyaki',
            dash_low_stock: 'Hannun Jari Ya Kusa Kare',

            banner_pending: 'Ana jiran amincewa da asusun. Don Allah jira.',
            banner_free: 'Shirin Kyauta — shago 1, manaja 1, ma\'aji 1 kawai.',
            banner_upgrade: 'Haɓaka ↗',
        },
    };

    const STORAGE_KEY = 'viARMS_lang';
    let currentLang = localStorage.getItem(STORAGE_KEY) || 'en';

    function t(key) {
        const dict = translations[currentLang] || translations['en'];
        return dict[key] || translations['en'][key] || key;
    }

    function applyTranslations() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translated = t(key);
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                el.placeholder = translated;
            } else if (el.tagName === 'OPTION') {
                el.textContent = translated;
            } else {
                // Preserve child elements (icons etc.) — only replace text nodes
                const iconChild = el.querySelector('i, svg');
                if (iconChild) {
                    // Element has an icon child — set text after the icon
                    el.childNodes.forEach(node => {
                        if (node.nodeType === Node.TEXT_NODE) {
                            node.textContent = ' ' + translated;
                        }
                    });
                    // If no text node existed, append one
                    if (![...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim())) {
                        el.appendChild(document.createTextNode(' ' + translated));
                    }
                } else {
                    el.textContent = translated;
                }
            }
        });

        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            el.placeholder = t(key);
        });

        // Sync all language selectors
        document.querySelectorAll('#lang-select-sidebar, #lang-select-mobile, #lang-select-login').forEach(sel => {
            sel.value = currentLang;
        });
    }

    function setLang(lang) {
        if (!translations[lang]) return;
        currentLang = lang;
        localStorage.setItem(STORAGE_KEY, lang);
        applyTranslations();
    }

    // Run on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyTranslations);
    } else {
        applyTranslations();
    }

    return { t, setLang, getCurrentLang: () => currentLang };
})();
