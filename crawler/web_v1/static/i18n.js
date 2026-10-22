/* CrawlMeme localization layer (Phase 14).
 *
 * Eight languages — the set the original live site offers: English (source), 简体中文, Español, Français, Deutsch,
 * Bahasa Indonesia, Filipino, Tiếng Việt.
 *
 * How it works: the pages are written in English. This script translates text nodes and the placeholder / title /
 * aria-label attributes in place, and keeps doing so as the page changes (MutationObserver), so it works on every page
 * without touching their rendering code.
 *   PHRASES   exact UI strings (labels, headings, buttons, column names, panel texts)
 *   PATTERNS  the engine's dynamic risk explanations (verdict reasons, gates, headlines, rug scenario, radar reasons,
 *             coverage notes), as anchored regular expressions with templates: $n inserts a captured group as is,
 *             %n translates it recursively (a reason inside a verdict line, a tail inside a headline)
 * Numbers, addresses, tickers and prices are never translated: only whole strings that match.
 * Adding a language: add its code to LANGS and NAME, then its column to each entry. A missing entry falls back to
 * English, never to an empty string.
 * Choice: ?lang=xx, then the saved choice (localStorage), then the browser language, then English.
 */
(function () {
  'use strict';
  var LANGS = ['en', 'zh', 'es', 'fr', 'de', 'id', 'fil', 'vi'];
  var NAME = {en: 'English', zh: '简体中文', es: 'Español', fr: 'Français', de: 'Deutsch', id: 'Bahasa Indonesia',
              fil: 'Filipino', vi: 'Tiếng Việt'};
  var KEY = 'cm_lang';

  // [en, zh, es, fr, de, id, fil, vi]
  var P = [
    // navigation and hero
    ['how it works', '工作原理', 'cómo funciona', 'comment ça marche', 'so funktioniert es', 'cara kerja', 'paano ito gumagana', 'cách hoạt động'],
    ['timeline', '时间线', 'cronología', 'chronologie', 'Zeitleiste', 'linimasa', 'timeline', 'dòng thời gian'],
    ['radar', '雷达', 'radar', 'radar', 'Radar', 'radar', 'radar', 'radar'],
    ['proof', '证明', 'pruebas', 'preuves', 'Nachweis', 'bukti', 'patunay', 'bằng chứng'],
    ['memecoin holder scanner ·', 'Meme 币持有者扫描器 ·', 'escáner de holders de memecoins ·', 'scanner de détenteurs de memecoins ·', 'Memecoin-Holder-Scanner ·', 'pemindai holder memecoin ·', 'scanner ng holder ng memecoin ·', 'trình quét holder memecoin ·'],
    ["Judges a token's real potential by its real holders. Six crawlers walk the top wallets, unmask the ones run by one person, and return a risk verdict in about 15 seconds.",
     '用真实持有者判断代币的真实潜力。六只爬虫逐个检查头部钱包，揪出由同一人操控的钱包，约 15 秒给出风险结论。',
     'Juzga el potencial real de un token por sus holders reales. Seis crawlers recorren las carteras principales, desenmascaran las que controla una sola persona y dan un veredicto de riesgo en unos 15 segundos.',
     "Juge le vrai potentiel d'un token d'après ses vrais détenteurs. Six crawlers parcourent les principaux portefeuilles, démasquent ceux tenus par une seule personne et rendent un verdict de risque en 15 secondes environ.",
     'Beurteilt das echte Potenzial eines Tokens anhand seiner echten Holder. Sechs Crawler gehen die größten Wallets durch, entlarven die, die einer einzigen Person gehören, und liefern in etwa 15 Sekunden ein Risikourteil.',
     'Menilai potensi asli token dari holder aslinya. Enam crawler menelusuri dompet teratas, membongkar dompet yang dikendalikan satu orang, dan memberi vonis risiko dalam sekitar 15 detik.',
     'Hinuhusgahan ang tunay na potensyal ng token batay sa tunay na mga holder nito. Anim na crawler ang sumusuri sa mga nangungunang wallet, inilalantad ang mga hawak ng iisang tao, at nagbibigay ng hatol sa panganib sa loob ng mga 15 segundo.',
     'Đánh giá tiềm năng thật của token qua những holder thật. Sáu crawler duyệt các ví lớn nhất, vạch mặt những ví do một người điều khiển và đưa ra kết luận rủi ro trong khoảng 15 giây.'],
    ['The first crawler scanner for Robinhood, Solana, Base and BNB memecoins', '首个面向 Robinhood、Solana、Base 和 BNB Meme 币的爬虫扫描器', 'El primer escáner con crawlers para memecoins de Robinhood, Solana, Base y BNB', 'Le premier scanner à crawlers pour les memecoins Robinhood, Solana, Base et BNB', 'Der erste Crawler-Scanner für Memecoins auf Robinhood, Solana, Base und BNB', 'Pemindai crawler pertama untuk memecoin Robinhood, Solana, Base, dan BNB', 'Ang unang crawler scanner para sa mga memecoin ng Robinhood, Solana, Base at BNB', 'Trình quét crawler đầu tiên cho memecoin trên Robinhood, Solana, Base và BNB'],
    ['crawl →', '爬取 →', 'rastrear →', 'crawler →', 'crawlen →', 'telusuri →', 'i-crawl →', 'quét →'],
    ['read-only · no wallet connect ·', '只读 · 无需连接钱包 ·', 'solo lectura · sin conectar cartera ·', 'lecture seule · sans connexion de portefeuille ·', 'nur lesen · keine Wallet-Verbindung ·', 'hanya baca · tanpa sambung dompet ·', 'read-only · walang pagkonekta ng wallet ·', 'chỉ đọc · không cần kết nối ví ·'],
    ['try a sample →', '试试示例 →', 'probar un ejemplo →', 'essayer un exemple →', 'Beispiel testen →', 'coba contoh →', 'subukan ang halimbawa →', 'thử ví dụ →'],
    ['recently scanned', '最近扫描', 'escaneados recientemente', 'scannés récemment', 'zuletzt gescannt', 'baru dipindai', 'kamakailang na-scan', 'vừa quét'],
    ['click to open', '点击打开', 'clic para abrir', 'cliquer pour ouvrir', 'zum Öffnen klicken', 'klik untuk membuka', 'i-click para buksan', 'nhấp để mở'],
    ['too early', '太早', 'muy pronto', 'trop tôt', 'zu früh', 'terlalu dini', 'masyadong maaga', 'quá sớm'],
    ['crawlers at work', '爬虫工作中', 'crawlers trabajando', 'crawlers au travail', 'Crawler bei der Arbeit', 'crawler bekerja', 'mga crawler sa trabaho', 'crawler đang làm việc'],
    ['live', '实时', 'en vivo', 'en direct', 'live', 'langsung', 'live', 'trực tiếp'],
    ['what comes next', '接下来', 'lo que viene', 'la suite', 'als Nächstes', 'berikutnya', 'susunod', 'sắp tới'],
    ['read-only, holds no keys', '只读，不持有任何密钥', 'solo lectura, no guarda claves', 'lecture seule, ne détient aucune clé', 'nur lesen, speichert keine Schlüssel', 'hanya baca, tidak menyimpan kunci', 'read-only, walang hawak na key', 'chỉ đọc, không giữ khóa'],
    ['menu', '菜单', 'menú', 'menu', 'Menü', 'menu', 'menu', 'menu'],
    ['copy full address', '复制完整地址', 'copiar dirección completa', "copier l'adresse complète", 'vollständige Adresse kopieren', 'salin alamat lengkap', 'kopyahin ang buong address', 'sao chép địa chỉ đầy đủ'],
    ['copy token address', '复制代币地址', 'copiar dirección del token', "copier l'adresse du token", 'Token-Adresse kopieren', 'salin alamat token', 'kopyahin ang address ng token', 'sao chép địa chỉ token'],
    ['0x… Robinhood token contract address', '0x… Robinhood 代币合约地址', '0x… dirección del contrato del token en Robinhood', '0x… adresse du contrat du token Robinhood', '0x… Robinhood-Token-Vertragsadresse', '0x… alamat kontrak token Robinhood', '0x… address ng kontrata ng token sa Robinhood', '0x… địa chỉ hợp đồng token Robinhood'],
    // how it works
    ['Read the holders', '读取持有者', 'Leer los holders', 'Lire les détenteurs', 'Holder auslesen', 'Baca para holder', 'Basahin ang mga holder', 'Đọc danh sách holder'],
    ['Walk each history', '追溯每段历史', 'Recorrer cada historial', 'Parcourir chaque historique', 'Jede Historie durchgehen', 'Telusuri setiap riwayat', 'Suriin ang bawat kasaysayan', 'Lần theo từng lịch sử'],
    ['Tie wallets together', '关联钱包', 'Vincular carteras', 'Relier les portefeuilles', 'Wallets verknüpfen', 'Hubungkan dompet', 'Pag-ugnayin ang mga wallet', 'Nối các ví với nhau'],
    ['Count the people', '统计真实人数', 'Contar a las personas', 'Compter les personnes', 'Die Personen zählen', 'Hitung orangnya', 'Bilangin ang mga tao', 'Đếm số người thật'],
    ['proven · direct on-chain tie', '已证实 · 直接链上关联', 'probado · vínculo directo on-chain', 'prouvé · lien direct on-chain', 'bewiesen · direkte On-Chain-Verbindung', 'terbukti · kaitan langsung on-chain', 'napatunayan · direktang ugnayan sa chain', 'đã chứng minh · liên kết on-chain trực tiếp'],
    ['pack · moved together', '团伙 · 同步行动', 'manada · se movieron juntas', 'meute · ont bougé ensemble', 'Rudel · gemeinsam bewegt', 'kawanan · bergerak bersama', 'pangkat · sabay na gumalaw', 'bầy · di chuyển cùng nhau'],
    // result header and verdict
    ['crawled', '已爬取', 'rastreado', 'crawlé', 'gecrawlt', 'ditelusuri', 'na-crawl', 'đã quét'],
    ['done', '完成', 'listo', 'terminé', 'fertig', 'selesai', 'tapos', 'xong'],
    ['stage', '阶段', 'etapa', 'étape', 'Phase', 'tahap', 'yugto', 'giai đoạn'],
    ['linking wallets', '正在关联钱包', 'vinculando carteras', 'liaison des portefeuilles', 'Wallets werden verknüpft', 'menghubungkan dompet', 'pinag-uugnay ang mga wallet', 'đang nối ví'],
    ['verdict', '结论', 'veredicto', 'verdict', 'Urteil', 'vonis', 'hatol', 'kết luận'],
    ['mcap', '市值', 'cap. mercado', 'cap. marché', 'Marktkap.', 'kap. pasar', 'market cap', 'vốn hóa'],
    ['liquidity', '流动性', 'liquidez', 'liquidité', 'Liquidität', 'likuiditas', 'liquidity', 'thanh khoản'],
    ['vol 24h', '24 小时成交量', 'vol. 24 h', 'vol. 24 h', 'Vol. 24 h', 'vol 24 j', 'vol 24h', 'KL 24h'],
    ['age', '年龄', 'edad', 'âge', 'Alter', 'usia', 'edad', 'tuổi'],
    ['price', '价格', 'precio', 'prix', 'Preis', 'harga', 'presyo', 'giá'],
    ['holders', '持有者', 'holders', 'détenteurs', 'Holder', 'holder', 'mga holder', 'holder'],
    ['total supply', '总供应量', 'suministro total', 'offre totale', 'Gesamtmenge', 'total suplai', 'kabuuang supply', 'tổng cung'],
    ['circulating', '流通量', 'en circulación', 'en circulation', 'im Umlauf', 'beredar', 'umiikot', 'lưu hành'],
    ['pool', '池子', 'pool', 'pool', 'Pool', 'pool', 'pool', 'pool'],
    ['top 10 share', '前 10 占比', 'participación top 10', 'part du top 10', 'Anteil Top 10', 'porsi 10 teratas', 'bahagi ng top 10', 'tỷ lệ top 10'],
    ['wallets crawled', '已爬取钱包', 'carteras rastreadas', 'portefeuilles crawlés', 'gecrawlte Wallets', 'dompet ditelusuri', 'mga wallet na na-crawl', 'ví đã quét'],
    ['not enough trades to chart yet', '交易太少，暂无法绘图', 'aún no hay suficientes operaciones para el gráfico', 'pas encore assez de transactions pour un graphique', 'noch zu wenige Trades für ein Diagramm', 'belum cukup transaksi untuk grafik', 'kulang pa ang trade para sa chart', 'chưa đủ giao dịch để vẽ biểu đồ'],
    ['now', '当前', 'ahora', 'maintenant', 'jetzt', 'sekarang', 'ngayon', 'hiện tại'],
    ['why probably rug', '为何可能跑路', 'por qué probablemente rug', 'pourquoi probablement un rug', 'warum wahrscheinlich ein Rug', 'mengapa kemungkinan rug', 'bakit malamang rug', 'vì sao có thể rug'],
    ['linked wallets', '关联钱包', 'carteras vinculadas', 'portefeuilles liés', 'verknüpfte Wallets', 'dompet terhubung', 'mga naka-ugnay na wallet', 'ví liên kết'],
    ['received by transfer', '通过转账获得', 'recibido por transferencia', 'reçu par transfert', 'per Transfer erhalten', 'diterima lewat transfer', 'natanggap sa transfer', 'nhận qua chuyển khoản'],
    ['fresh wallets', '新钱包', 'carteras nuevas', 'portefeuilles neufs', 'frische Wallets', 'dompet baru', 'mga bagong wallet', 'ví mới'],
    ['bundle / snipers', '捆绑 / 狙击', 'bundle / snipers', 'bundle / snipers', 'Bundle / Sniper', 'bundle / sniper', 'bundle / sniper', 'bundle / sniper'],
    ['crew from other tokens', '其他代币中的团伙', 'grupo de otros tokens', "équipe d'autres tokens", 'Crew aus anderen Tokens', 'kru dari token lain', 'grupo mula sa ibang token', 'nhóm từ token khác'],
    ['Trade on Axiom ↗', '在 Axiom 交易 ↗', 'Operar en Axiom ↗', 'Trader sur Axiom ↗', 'Auf Axiom handeln ↗', 'Trading di Axiom ↗', 'Mag-trade sa Axiom ↗', 'Giao dịch trên Axiom ↗'],
    ['operator (dump impact)', '操盘方（抛售冲击）', 'operador (impacto de venta)', 'opérateur (impact de vente)', 'Operator (Verkaufswirkung)', 'operator (dampak jual)', 'operator (epekto ng pagbenta)', 'nhà điều hành (tác động xả)'],
    ['concentration', '集中度', 'concentración', 'concentration', 'Konzentration', 'konsentrasi', 'konsentrasyon', 'mức tập trung'],
    ['copy link', '复制链接', 'copiar enlace', 'copier le lien', 'Link kopieren', 'salin tautan', 'kopyahin ang link', 'sao chép liên kết'],
    ['new crawl', '新的扫描', 'nuevo rastreo', 'nouveau crawl', 'neuer Crawl', 'telusuri baru', 'bagong crawl', 'quét mới'],
    ['Early buyers', '早期买家', 'Primeros compradores', 'Premiers acheteurs', 'Frühe Käufer', 'Pembeli awal', 'Mga unang bumili', 'Người mua sớm'],
    ['first 20 after launch', '上线后前 20 个', 'los primeros 20 tras el lanzamiento', 'les 20 premiers après le lancement', 'die ersten 20 nach dem Start', '20 pertama setelah peluncuran', 'unang 20 pagkatapos ng launch', '20 người đầu sau khi ra mắt'],
    // holder table and flags
    ['wallet', '钱包', 'cartera', 'portefeuille', 'Wallet', 'dompet', 'wallet', 'ví'],
    ['share', '占比', 'participación', 'part', 'Anteil', 'porsi', 'bahagi', 'tỷ lệ'],
    ['flags', '标记', 'señales', 'signaux', 'Merkmale', 'tanda', 'mga flag', 'cờ'],
    ['deployer', '部署者', 'desplegador', 'déployeur', 'Deployer', 'deployer', 'deployer', 'người triển khai'],
    ['virgin', '新号', 'virgen', 'vierge', 'jungfräulich', 'perawan', 'bago', 'ví trắng'],
    ['transfer', '转账', 'transferencia', 'transfert', 'Transfer', 'transfer', 'transfer', 'chuyển khoản'],
    ['clean', '干净', 'limpia', 'propre', 'sauber', 'bersih', 'malinis', 'sạch'],
    ['sniper', '狙击', 'sniper', 'sniper', 'Sniper', 'sniper', 'sniper', 'sniper'],
    ['short history', '历史短', 'historial corto', 'historique court', 'kurze Historie', 'riwayat pendek', 'maikling kasaysayan', 'lịch sử ngắn'],
    ['link', '关联', 'vínculo', 'lien', 'Verbindung', 'tautan', 'ugnay', 'liên kết'],
    ['balances', '余额', 'saldos', 'soldes', 'Salden', 'saldo', 'mga balanse', 'số dư'],
    ['share of float', '流通占比', 'participación del float', 'part du flottant', 'Anteil am Float', 'porsi float', 'bahagi ng float', 'tỷ lệ lưu hành'],
    ['entry block', '买入区块', 'bloque de entrada', "bloc d'entrée", 'Einstiegsblock', 'blok masuk', 'block ng pagpasok', 'khối vào'],
    ['sold', '已卖出', 'vendió', 'vendu', 'verkauft', 'terjual', 'naibenta', 'đã bán'],
    ['buy', '买入', 'compra', 'achat', 'Kauf', 'beli', 'bili', 'mua'],
    ['burned', '已销毁', 'quemado', 'brûlé', 'verbrannt', 'dibakar', 'nasunog', 'đã đốt'],
    // panels added by CrawlMeme
    ['launch bundle', '上线捆绑', 'bundle de lanzamiento', 'bundle de lancement', 'Launch-Bundle', 'bundle peluncuran', 'launch bundle', 'gói ra mắt'],
    ['high confidence', '高可信度', 'confianza alta', 'confiance élevée', 'hohe Sicherheit', 'keyakinan tinggi', 'mataas na kumpiyansa', 'độ tin cậy cao'],
    ['medium confidence', '中等可信度', 'confianza media', 'confiance moyenne', 'mittlere Sicherheit', 'keyakinan sedang', 'katamtamang kumpiyansa', 'độ tin cậy trung bình'],
    ['low confidence', '低可信度', 'confianza baja', 'confiance faible', 'geringe Sicherheit', 'keyakinan rendah', 'mababang kumpiyansa', 'độ tin cậy thấp'],
    ['initial allocation', '初始分配', 'asignación inicial', 'allocation initiale', 'Anfangszuteilung', 'alokasi awal', 'paunang alokasyon', 'phân bổ ban đầu'],
    ['still traceable', '仍可追踪', 'aún rastreable', 'encore traçable', 'noch verfolgbar', 'masih terlacak', 'masusundan pa', 'vẫn truy được'],
    ['outside the top holders', '在头部持有者之外', 'fuera de los principales holders', 'hors des principaux détenteurs', 'außerhalb der Top-Holder', 'di luar holder teratas', 'labas sa mga nangungunang holder', 'ngoài nhóm holder lớn'],
    ['burned / locked', '销毁 / 锁定', 'quemado / bloqueado', 'brûlé / verrouillé', 'verbrannt / gesperrt', 'dibakar / dikunci', 'nasunog / naka-lock', 'đốt / khóa'],
    ['current holders', '当前持有者', 'holders actuales', 'détenteurs actuels', 'aktuelle Holder', 'holder saat ini', 'kasalukuyang mga holder', 'holder hiện tại'],
    ['launch wallet', '上线钱包', 'cartera de lanzamiento', 'portefeuille de lancement', 'Launch-Wallet', 'dompet peluncuran', 'launch wallet', 'ví ra mắt'],
    ['how', '方式', 'cómo', 'comment', 'wie', 'cara', 'paano', 'cách'],
    ['took', '获得', 'tomó', 'a pris', 'erhalten', 'mengambil', 'kinuha', 'đã lấy'],
    ['holds', '持有', 'tiene', 'détient', 'hält', 'memegang', 'hawak', 'đang giữ'],
    ['sell impact', '抛售冲击', 'impacto de venta', 'impact de vente', 'Verkaufswirkung', 'dampak jual', 'epekto ng pagbenta', 'tác động bán'],
    ['if they sell', '如果他们卖出', 'si venden', "s'ils vendent", 'wenn sie verkaufen', 'jika mereka menjual', 'kung magbenta sila', 'nếu họ bán'],
    ['price impact', '价格冲击', 'impacto en el precio', 'impact sur le prix', 'Preiswirkung', 'dampak harga', 'epekto sa presyo', 'tác động giá'],
    ['avg slippage', '平均滑点', 'deslizamiento medio', 'slippage moyen', 'Ø-Slippage', 'slippage rata-rata', 'avg na slippage', 'trượt giá TB'],
    ['proceeds', '所得', 'ingresos', 'produit', 'Erlös', 'hasil', 'kita', 'tiền thu'],
    ['largest wallet', '最大钱包', 'cartera más grande', 'plus gros portefeuille', 'größte Wallet', 'dompet terbesar', 'pinakamalaking wallet', 'ví lớn nhất'],
    ['largest linked operator', '最大关联操盘方', 'mayor operador vinculado', 'plus gros opérateur lié', 'größter verknüpfter Operator', 'operator terhubung terbesar', 'pinakamalaking naka-ugnay na operator', 'nhà điều hành liên kết lớn nhất'],
    ['all suspicious supply', '全部可疑筹码', 'todo el suministro sospechoso', "toute l'offre suspecte", 'gesamte verdächtige Menge', 'semua suplai mencurigakan', 'lahat ng kahina-hinalang supply', 'toàn bộ nguồn cung đáng ngờ'],
    ['launch supply, traced', '上线筹码（已追踪）', 'suministro de lanzamiento, rastreado', 'offre de lancement, tracée', 'Launch-Menge, verfolgt', 'suplai peluncuran, terlacak', 'launch supply, nasundan', 'nguồn cung ra mắt, đã truy'],
    ['priced with', '定价方式', 'valorado con', 'évalué avec', 'bewertet mit', 'dihargai dengan', 'tinantya gamit', 'định giá bằng'],
    ['sell tax', '卖出税', 'impuesto de venta', 'taxe de vente', 'Verkaufssteuer', 'pajak jual', 'sell tax', 'thuế bán'],
    ['none found', '未发现', 'ninguno', 'aucune trouvée', 'keine gefunden', 'tidak ditemukan', 'walang nakita', 'không thấy'],
    ['depth', '深度', 'profundidad', 'profondeur', 'Tiefe', 'kedalaman', 'lalim', 'độ sâu'],
    ['how sure is this', '可信度如何', 'qué tan seguro es esto', 'quel degré de certitude', 'wie sicher ist das', 'seberapa yakin', 'gaano ito katiyak', 'mức chắc chắn'],
    ['coverage', '覆盖度', 'cobertura', 'couverture', 'Abdeckung', 'cakupan', 'saklaw', 'độ phủ'],
    ['full', '完整', 'completa', 'complète', 'vollständig', 'penuh', 'buo', 'đầy đủ'],
    ['partial', '部分', 'parcial', 'partielle', 'teilweise', 'sebagian', 'bahagya', 'một phần'],
    ['limited', '有限', 'limitada', 'limitée', 'eingeschränkt', 'terbatas', 'limitado', 'hạn chế'],
    ['wallets read', '已读钱包', 'carteras leídas', 'portefeuilles lus', 'gelesene Wallets', 'dompet terbaca', 'mga wallet na nabasa', 'ví đã đọc'],
    ['holder balances', '持有者余额', 'saldos de holders', 'soldes des détenteurs', 'Holder-Salden', 'saldo holder', 'balanse ng mga holder', 'số dư holder'],
    ['market data', '市场数据', 'datos de mercado', 'données de marché', 'Marktdaten', 'data pasar', 'market data', 'dữ liệu thị trường'],
    ['prices for P&L', '盈亏价格', 'precios para P&L', 'prix pour le P&L', 'Preise für G&V', 'harga untuk P&L', 'presyo para sa P&L', 'giá cho lãi/lỗ'],
    ['funding read', '已读资金来源', 'financiación leída', 'financement lu', 'Finanzierung gelesen', 'pendanaan terbaca', 'nabasang pondo', 'nguồn vốn đã đọc'],
    ['memory', '记忆库', 'memoria', 'mémoire', 'Gedächtnis', 'memori', 'memorya', 'bộ nhớ'],
    ['cost', '成本', 'costo', 'coût', 'Kosten', 'biaya', 'gastos', 'chi phí'],
    ['relationship graph', '关系图', 'grafo de relaciones', 'graphe des relations', 'Beziehungsgraph', 'grafik hubungan', 'graph ng ugnayan', 'đồ thị quan hệ'],
    ['token distributor', '代币分发者', 'distribuidor del token', 'distributeur du token', 'Token-Verteiler', 'distributor token', 'distributor ng token', 'nhà phân phối token'],
    ['proven link', '已证实关联', 'vínculo probado', 'lien prouvé', 'bewiesene Verbindung', 'tautan terbukti', 'napatunayang ugnay', 'liên kết đã chứng minh'],
    ['same-block pack', '同区块团伙', 'manada del mismo bloque', 'meute du même bloc', 'Rudel im selben Block', 'kawanan blok sama', 'pangkat sa parehong block', 'bầy cùng khối'],
    ['wallet forensics', '钱包取证', 'análisis forense de carteras', 'analyse des portefeuilles', 'Wallet-Forensik', 'forensik dompet', 'forensics ng wallet', 'pháp chứng ví'],
    ['hub funder', '枢纽资金方', 'financiador hub', 'financeur hub', 'Hub-Finanzierer', 'pendana hub', 'hub na nagpopondo', 'nguồn vốn trung tâm'],
    ['entry', '买入', 'entrada', 'entrée', 'Einstieg', 'masuk', 'pasok', 'vào'],
    ['held', '持有时长', 'mantenido', 'détenu', 'gehalten', 'ditahan', 'hinawakan', 'đã giữ'],
    ['sold for', '卖出所得', 'vendido por', 'vendu pour', 'verkauft für', 'terjual seharga', 'naibenta sa halagang', 'bán được'],
    ['holding', '持仓', 'en cartera', 'en portefeuille', 'Bestand', 'dipegang', 'hawak', 'đang giữ'],
    ['funded by', '资金来源', 'financiado por', 'financé par', 'finanziert von', 'didanai oleh', 'pinondohan ng', 'được cấp vốn bởi'],
    ['seen before', '曾出现过', 'visto antes', 'déjà vu', 'schon gesehen', 'pernah terlihat', 'nakita na dati', 'đã từng thấy'],
    ['crew', '团伙', 'grupo', 'équipe', 'Crew', 'kru', 'grupo', 'nhóm'],
    ['serial deployer', '连续部署者', 'desplegador en serie', 'déployeur en série', 'Serien-Deployer', 'deployer berantai', 'serial deployer', 'người triển khai hàng loạt'],
    ['repeat funder', '重复资金方', 'financiador repetido', 'financeur récurrent', 'wiederkehrender Finanzierer', 'pendana berulang', 'paulit-ulit na nagpopondo', 'nguồn vốn lặp lại'],
    ['rug history', '跑路历史', 'historial de rugs', 'historique de rugs', 'Rug-Vorgeschichte', 'riwayat rug', 'kasaysayan ng rug', 'lịch sử rug'],
    ['dev group', '开发者团体', 'grupo de devs', 'groupe de devs', 'Dev-Gruppe', 'grup dev', 'grupo ng dev', 'nhóm dev'],
    ['recurring cluster', '重复出现的集群', 'clúster recurrente', 'groupe récurrent', 'wiederkehrender Cluster', 'klaster berulang', 'paulit-ulit na cluster', 'cụm lặp lại'],
    ['scan history', '扫描历史', 'historial de escaneos', 'historique des scans', 'Scan-Verlauf', 'riwayat pindaian', 'kasaysayan ng scan', 'lịch sử quét'],
    ['your recent scans', '你最近的扫描', 'tus escaneos recientes', 'vos scans récents', 'deine letzten Scans', 'pindaian terakhir Anda', 'iyong mga kamakailang scan', 'lượt quét gần đây của bạn'],
    ['clear', '清除', 'borrar', 'effacer', 'löschen', 'hapus', 'burahin', 'xóa'],
    ['share & export', '分享与导出', 'compartir y exportar', 'partager et exporter', 'teilen & exportieren', 'bagikan & ekspor', 'ibahagi at i-export', 'chia sẻ & xuất'],
    ['copy report link', '复制报告链接', 'copiar enlace del informe', 'copier le lien du rapport', 'Berichtslink kopieren', 'salin tautan laporan', 'kopyahin ang link ng ulat', 'sao chép liên kết báo cáo'],
    ['download report (JSON)', '下载报告（JSON）', 'descargar informe (JSON)', 'télécharger le rapport (JSON)', 'Bericht herunterladen (JSON)', 'unduh laporan (JSON)', 'i-download ang ulat (JSON)', 'tải báo cáo (JSON)'],
    ['download report (text)', '下载报告（文本）', 'descargar informe (texto)', 'télécharger le rapport (texte)', 'Bericht herunterladen (Text)', 'unduh laporan (teks)', 'i-download ang ulat (text)', 'tải báo cáo (văn bản)'],
    ['download share card', '下载分享卡片', 'descargar tarjeta para compartir', 'télécharger la carte de partage', 'Teilkarte herunterladen', 'unduh kartu berbagi', 'i-download ang share card', 'tải thẻ chia sẻ'],
    ['copied', '已复制', 'copiado', 'copié', 'kopiert', 'tersalin', 'nakopya', 'đã sao chép'],
    // radar and proof pages
    ['launch radar', '上线雷达', 'radar de lanzamientos', 'radar des lancements', 'Launch-Radar', 'radar peluncuran', 'launch radar', 'radar ra mắt'],
    ['on radar', '雷达上', 'en el radar', 'sur le radar', 'auf dem Radar', 'di radar', 'nasa radar', 'trên radar'],
    ['dropped', '已移除', 'descartados', 'retirés', 'entfernt', 'dihapus', 'tinanggal', 'đã loại'],
    ['rescan', '重新扫描', 'reescanear', 'rescanner', 'neu scannen', 'pindai ulang', 'i-scan muli', 'quét lại'],
    ['scanning…', '扫描中…', 'escaneando…', 'scan en cours…', 'scanne…', 'memindai…', 'nag-i-scan…', 'đang quét…'],
    ['active', '有效', 'activo', 'actif', 'aktiv', 'aktif', 'aktibo', 'hoạt động'],
    ['downgraded', '已降级', 'degradado', 'rétrogradé', 'herabgestuft', 'diturunkan', 'ibinaba', 'bị hạ'],
    ['removed', '已移除', 'eliminado', 'retiré', 'entfernt', 'dihapus', 'tinanggal', 'đã gỡ'],
    ['chain', '链', 'cadena', 'chaîne', 'Chain', 'chain', 'chain', 'chuỗi'],
    ['launchpad', '发射平台', 'launchpad', 'launchpad', 'Launchpad', 'launchpad', 'launchpad', 'launchpad'],
    ['window', '时间窗', 'ventana', 'fenêtre', 'Zeitfenster', 'jendela', 'window', 'khung thời gian'],
    ['verdicts', '结论', 'veredictos', 'verdicts', 'Urteile', 'vonis', 'mga hatol', 'kết luận'],
    ['all', '全部', 'todos', 'tous', 'alle', 'semua', 'lahat', 'tất cả'],
    ['token', '代币', 'token', 'token', 'Token', 'token', 'token', 'token'],
    ['scanned', '扫描时间', 'escaneado', 'scanné', 'gescannt', 'dipindai', 'na-scan', 'đã quét'],
    ['market cap', '市值', 'cap. de mercado', 'capitalisation', 'Marktkapitalisierung', 'kapitalisasi pasar', 'market cap', 'vốn hóa'],
    ['since scan', '扫描以来', 'desde el escaneo', 'depuis le scan', 'seit dem Scan', 'sejak pindaian', 'mula sa scan', 'từ lúc quét'],
    ['bundle', '捆绑', 'bundle', 'bundle', 'Bundle', 'bundle', 'bundle', 'bundle'],
    ['operator', '操盘方', 'operador', 'opérateur', 'Operator', 'operator', 'operator', 'nhà điều hành'],
    ['status', '状态', 'estado', 'statut', 'Status', 'status', 'status', 'trạng thái'],
    ['proof of work', '工作证明', 'prueba de trabajo', 'preuve de travail', 'Arbeitsnachweis', 'bukti kerja', 'patunay ng trabaho', 'bằng chứng làm việc'],
    ['rug replay', '跑路回放', 'repetición de rugs', 'replay des rugs', 'Rug-Replay', 'replay rug', 'rug replay', 'xem lại rug'],
    ['gem replay', '宝石回放', 'repetición de gemas', 'replay des pépites', 'Gem-Replay', 'replay permata', 'gem replay', 'xem lại gem'],
    ['eligible scans', '符合条件的扫描', 'escaneos elegibles', 'scans éligibles', 'berücksichtigte Scans', 'pindaian yang memenuhi syarat', 'mga kwalipikadong scan', 'lượt quét đủ điều kiện'],
    ['resolved', '已判定', 'resueltos', 'résolus', 'aufgelöst', 'terselesaikan', 'nalutas', 'đã xác định'],
    ['unresolved', '未判定', 'sin resolver', 'non résolus', 'offen', 'belum terselesaikan', 'hindi pa nalulutas', 'chưa xác định'],
    ['base rate', '基础比率', 'tasa base', 'taux de base', 'Basisrate', 'tingkat dasar', 'base rate', 'tỷ lệ nền'],
    ['precision', '精确率', 'precisión', 'précision', 'Präzision', 'presisi', 'precision', 'độ chính xác'],
    ['recall', '召回率', 'exhaustividad', 'rappel', 'Trefferquote', 'recall', 'recall', 'độ phủ (recall)'],
    ['event happened', '事件发生', 'el evento ocurrió', "l'événement a eu lieu", 'Ereignis eingetreten', 'peristiwa terjadi', 'nangyari ang pangyayari', 'sự kiện đã xảy ra'],
    ['did not happen', '未发生', 'no ocurrió', "n'a pas eu lieu", 'nicht eingetreten', 'tidak terjadi', 'hindi nangyari', 'không xảy ra'],
    ['flagged', '已标记', 'marcado', 'signalé', 'markiert', 'ditandai', 'na-flag', 'bị gắn cờ'],
    ['not flagged', '未标记', 'no marcado', 'non signalé', 'nicht markiert', 'tidak ditandai', 'hindi na-flag', 'không gắn cờ'],
    ['supporting data', '佐证数据', 'datos de respaldo', 'données justificatives', 'Belegdaten', 'data pendukung', 'sumusuportang data', 'dữ liệu chứng minh'],
    ['record', '记录', 'registro', 'registre', 'Datensatz', 'catatan', 'record', 'bản ghi'],
    ['explorer', '浏览器', 'explorador', 'explorateur', 'Explorer', 'penjelajah', 'explorer', 'trình khám phá'],
    ['language', '语言', 'idioma', 'langue', 'Sprache', 'bahasa', 'wika', 'ngôn ngữ'],
    // share & export, scan history, graph controls (Phase 14)
    ['copy share card', '复制分享卡片', 'copiar tarjeta para compartir', 'copier la carte de partage', 'Teilkarte kopieren', 'salin kartu berbagi', 'kopyahin ang share card', 'sao chép thẻ chia sẻ'],
    ['not copied', '未能复制', 'no se copió', 'non copié', 'nicht kopiert', 'gagal disalin', 'hindi nakopya', 'chưa sao chép được'],
    ['scan report', '扫描报告', 'informe de escaneo', 'rapport de scan', 'Scan-Bericht', 'laporan pindaian', 'ulat ng scan', 'báo cáo quét'],
    ['address', '地址', 'dirección', 'adresse', 'Adresse', 'alamat', 'address', 'địa chỉ'],
    ['report link', '报告链接', 'enlace del informe', 'lien du rapport', 'Berichtslink', 'tautan laporan', 'link ng ulat', 'liên kết báo cáo'],
    ['generated', '生成时间', 'generado', 'généré', 'erstellt', 'dibuat', 'ginawa', 'tạo lúc'],
    ['score', '分数', 'puntuación', 'score', 'Score', 'skor', 'iskor', 'điểm'],
    ['outcome', '结果', 'resultado', 'résultat', 'Ergebnis', 'hasil', 'kinalabasan', 'kết quả'],
    ['records', '条记录', 'registros', 'registres', 'Datensätze', 'catatan', 'record', 'bản ghi'],
    ['immutable, fingerprinted', '不可篡改，已指纹化', 'inmutables, con huella', 'immuables, avec empreinte', 'unveränderlich, mit Fingerabdruck', 'tidak bisa diubah, bersidik', 'hindi nababago, may fingerprint', 'bất biến, có dấu vân tay'],
    ['first scan', '首次扫描', 'primer escaneo', 'premier scan', 'erster Scan', 'pindaian pertama', 'unang scan', 'lần quét đầu'],
    ['latest market', '最新市场', 'mercado más reciente', 'marché le plus récent', 'aktueller Markt', 'pasar terbaru', 'pinakabagong market', 'thị trường mới nhất'],
    ['show:', '显示：', 'mostrar:', 'afficher :', 'anzeigen:', 'tampilkan:', 'ipakita:', 'hiển thị:'],
    ['hub funders', '枢纽资金方', 'financiadores hub', 'financeurs hub', 'Hub-Finanzierer', 'pendana hub', 'mga hub na nagpopondo', 'nguồn vốn trung tâm'],
    ['same-block packs', '同区块团伙', 'manadas del mismo bloque', 'meutes du même bloc', 'Rudel im selben Block', 'kawanan blok sama', 'mga pangkat sa parehong block', 'bầy cùng khối'],
    ['drag a node to move it · hover or tap to see its links · click opens the wallet', '拖动节点可移动 · 悬停或轻点查看其关联 · 点击打开钱包', 'arrastra un nodo para moverlo · pasa el cursor o toca para ver sus vínculos · clic abre la cartera', 'glissez un nœud pour le déplacer · survolez ou touchez pour voir ses liens · un clic ouvre le portefeuille', 'Knoten ziehen zum Verschieben · darüberfahren oder tippen zeigt seine Verbindungen · Klick öffnet die Wallet', 'seret simpul untuk memindahkan · arahkan atau ketuk untuk melihat tautannya · klik membuka dompet', 'i-drag ang node para ilipat · i-hover o i-tap para makita ang mga ugnay · binubuksan ng click ang wallet', 'kéo một nút để di chuyển · rê chuột hoặc chạm để xem liên kết · nhấp để mở ví'],
    ['drag a node to move it · hover or tap to see its links · click copies the address', '拖动节点可移动 · 悬停或轻点查看其关联 · 点击复制地址', 'arrastra un nodo para moverlo · pasa el cursor o toca para ver sus vínculos · clic copia la dirección', 'glissez un nœud pour le déplacer · survolez ou touchez pour voir ses liens · un clic copie l’adresse', 'Knoten ziehen zum Verschieben · darüberfahren oder tippen zeigt seine Verbindungen · Klick kopiert die Adresse', 'seret simpul untuk memindahkan · arahkan atau ketuk untuk melihat tautannya · klik menyalin alamat', 'i-drag ang node para ilipat · i-hover o i-tap para makita ang mga ugnay · kinokopya ng click ang address', 'kéo một nút để di chuyển · rê chuột hoặc chạm để xem liên kết · nhấp để sao chép địa chỉ'],
    ['Read-only analysis of public on-chain data. Not financial advice.', '基于公开链上数据的只读分析，不构成投资建议。', 'Análisis de solo lectura de datos públicos on-chain. No es asesoramiento financiero.', 'Analyse en lecture seule de données on-chain publiques. Pas un conseil financier.', 'Reine Leseanalyse öffentlicher On-Chain-Daten. Keine Finanzberatung.', 'Analisis hanya-baca atas data on-chain publik. Bukan nasihat keuangan.', 'Read-only na pagsusuri ng pampublikong on-chain data. Hindi ito payong pinansyal.', 'Phân tích chỉ đọc dữ liệu on-chain công khai. Không phải lời khuyên tài chính.'],
    ['just now', '刚刚', 'ahora mismo', "à l'instant", 'gerade eben', 'baru saja', 'ngayon lang', 'vừa xong'],
    ['probably rug', '可能跑路', 'probablemente rug', 'probablement un rug', 'wahrscheinlich Rug', 'kemungkinan rug', 'malamang rug', 'có thể rug'],
    // radar page
    ['min score', '最低分数', 'puntuación mínima', 'score minimum', 'Mindestscore', 'skor minimum', 'pinakamababang iskor', 'điểm tối thiểu'],
    ['max age (min)', '最大年龄（分钟）', 'edad máxima (min)', 'âge max (min)', 'Höchstalter (Min.)', 'usia maks (menit)', 'max na edad (min)', 'tuổi tối đa (phút)'],
    ['min market cap $', '最低市值 $', 'cap. de mercado mín. $', 'capitalisation min $', 'Mindest-Marktkap. $', 'kap. pasar min $', 'min na market cap $', 'vốn hóa tối thiểu $'],
    ['max market cap $', '最高市值 $', 'cap. de mercado máx. $', 'capitalisation max $', 'Höchst-Marktkap. $', 'kap. pasar maks $', 'max na market cap $', 'vốn hóa tối đa $'],
    ['min liquidity $', '最低流动性 $', 'liquidez mín. $', 'liquidité min $', 'Mindestliquidität $', 'likuiditas min $', 'min na liquidity $', 'thanh khoản tối thiểu $'],
    ['max bundle % supply', '捆绑最多占总量 %', 'bundle máx. % del suministro', 'bundle max % de l’offre', 'Bundle max. % der Menge', 'bundle maks % suplai', 'max na bundle % ng supply', 'gói tối đa % tổng cung'],
    ['clean + ok', 'clean + ok', 'clean + ok', 'clean + ok', 'clean + ok', 'clean + ok', 'clean + ok', 'clean + ok'],
    ['clean only', '仅 clean', 'solo clean', 'clean uniquement', 'nur clean', 'hanya clean', 'clean lang', 'chỉ clean'],
    ['up to risky', '最多到 risky', 'hasta risky', "jusqu'à risky", 'bis risky', 'sampai risky', 'hanggang risky', 'đến mức risky'],
    ['any', '不限', 'cualquiera', 'tous', 'beliebig', 'semua', 'kahit ano', 'bất kỳ'],
    ['failed', '失败', 'falló', 'échec', 'fehlgeschlagen', 'gagal', 'nabigo', 'thất bại'],
    ['update failed, retrying', '更新失败，正在重试', 'error al actualizar, reintentando', 'échec de la mise à jour, nouvel essai', 'Aktualisierung fehlgeschlagen, neuer Versuch', 'gagal memperbarui, mencoba lagi', 'nabigo ang update, sinusubukang muli', 'cập nhật thất bại, đang thử lại'],
    // proof page
    ['observation window', '观察窗口', 'ventana de observación', "fenêtre d'observation", 'Beobachtungsfenster', 'jendela pengamatan', 'window ng obserbasyon', 'khung quan sát'],
    ['scans from the last', '最近的扫描', 'escaneos de los últimos', 'scans des derniers', 'Scans der letzten', 'pindaian dalam', 'mga scan mula sa huling', 'lượt quét trong'],
    ['rug = market cap fell ≥ %', '跑路 = 市值下跌 ≥ %', 'rug = la cap. de mercado cayó ≥ %', 'rug = la capitalisation a chuté ≥ %', 'Rug = Marktkap. gefallen ≥ %', 'rug = kap. pasar turun ≥ %', 'rug = bumagsak ang market cap ≥ %', 'rug = vốn hóa giảm ≥ %'],
    ['gem = market cap rose ≥ ×', '宝石 = 市值上涨 ≥ ×', 'gema = la cap. de mercado subió ≥ ×', 'pépite = la capitalisation a monté ≥ ×', 'Gem = Marktkap. gestiegen ≥ ×', 'permata = kap. pasar naik ≥ ×', 'gem = tumaas ang market cap ≥ ×', 'gem = vốn hóa tăng ≥ ×'],
    ['measured', '测量方式', 'medido', 'mesuré', 'gemessen', 'diukur', 'sinukat', 'đo'],
    ['at any point in the window', '窗口内任意时刻', 'en cualquier momento de la ventana', 'à tout moment dans la fenêtre', 'zu jedem Zeitpunkt im Fenster', 'kapan pun dalam jendela', 'kahit kailan sa window', 'tại bất kỳ thời điểm nào trong khung'],
    ['at the window end', '窗口结束时', 'al final de la ventana', 'à la fin de la fenêtre', 'am Ende des Fensters', 'di akhir jendela', 'sa dulo ng window', 'cuối khung'],
    ['model', '模型', 'modelo', 'modèle', 'Modell', 'model', 'modelo', 'mô hình'],
    ['min market cap at scan $', '扫描时最低市值 $', 'cap. de mercado mín. al escanear $', 'capitalisation min au scan $', 'Mindest-Marktkap. beim Scan $', 'kap. pasar min saat dipindai $', 'min na market cap sa scan $', 'vốn hóa tối thiểu lúc quét $'],
    ['show', '显示', 'mostrar', 'afficher', 'anzeigen', 'tampilkan', 'ipakita', 'hiển thị'],
    ['default', '默认', 'predeterminado', 'par défaut', 'Standard', 'bawaan', 'default', 'mặc định'],
    ['true positives', '真阳性', 'verdaderos positivos', 'vrais positifs', 'richtig positive', 'positif benar', 'mga tunay na positibo', 'dương tính thật'],
    ['false positives', '假阳性', 'falsos positivos', 'faux positifs', 'falsch positive', 'positif palsu', 'mga maling positibo', 'dương tính giả'],
    ['false negatives', '假阴性', 'falsos negativos', 'faux négatifs', 'falsch negative', 'negatif palsu', 'mga maling negatibo', 'âm tính giả'],
    ['true negatives', '真阴性', 'verdaderos negativos', 'vrais négatifs', 'richtig negative', 'negatif benar', 'mga tunay na negatibo', 'âm tính thật'],
    ['true positive', '真阳性', 'verdadero positivo', 'vrai positif', 'richtig positiv', 'positif benar', 'tunay na positibo', 'dương tính thật'],
    ['false positive', '假阳性', 'falso positivo', 'faux positif', 'falsch positiv', 'positif palsu', 'maling positibo', 'dương tính giả'],
    ['false negative', '假阴性', 'falso negativo', 'faux négatif', 'falsch negativ', 'negatif palsu', 'maling negatibo', 'âm tính giả'],
    ['true negative', '真阴性', 'verdadero negativo', 'vrai négatif', 'richtig negativ', 'negatif benar', 'tunay na negatibo', 'âm tính thật'],
    ['scanned (UTC)', '扫描时间（UTC）', 'escaneado (UTC)', 'scanné (UTC)', 'gescannt (UTC)', 'dipindai (UTC)', 'na-scan (UTC)', 'đã quét (UTC)'],
    ['mcap at scan', '扫描时市值', 'cap. al escanear', 'capitalisation au scan', 'Marktkap. beim Scan', 'kap. saat dipindai', 'market cap sa scan', 'vốn hóa lúc quét'],
    ['later mcap', '之后市值', 'cap. posterior', 'capitalisation ultérieure', 'spätere Marktkap.', 'kap. setelahnya', 'market cap pagkatapos', 'vốn hóa sau đó'],
    ['peak', '峰值', 'máximo', 'pic', 'Hoch', 'puncak', 'pinakamataas', 'đỉnh'],
    ['trough', '谷值', 'mínimo', 'creux', 'Tief', 'lembah', 'pinakamababa', 'đáy'],
    ['evaluated (UTC)', '评估时间（UTC）', 'evaluado (UTC)', 'évalué (UTC)', 'bewertet (UTC)', 'dievaluasi (UTC)', 'sinuri (UTC)', 'đánh giá (UTC)'],
    ['rug', '跑路', 'rug', 'rug', 'Rug', 'rug', 'rug', 'rug'],
    ['gem', '宝石', 'gema', 'pépite', 'Gem', 'permata', 'gem', 'gem'],
    ['scan', '扫描', 'escaneo', 'scan', 'Scan', 'pindaian', 'scan', 'quét'],
    ['market', '市场', 'mercado', 'marché', 'Markt', 'pasar', 'market', 'thị trường'],
    ['open', '未结', 'abierto', 'ouvert', 'offen', 'terbuka', 'bukas', 'mở'],
    ['Rug Replay', '跑路回放', 'Repetición de rugs', 'Replay des rugs', 'Rug-Replay', 'Replay Rug', 'Rug Replay', 'Xem lại rug'],
    ['Gem Replay', '宝石回放', 'Repetición de gemas', 'Replay des pépites', 'Gem-Replay', 'Replay Permata', 'Gem Replay', 'Xem lại gem'],
    ['7 days', '7 天', '7 días', '7 jours', '7 Tage', '7 hari', '7 araw', '7 ngày'],
    ['30 days', '30 天', '30 días', '30 jours', '30 Tage', '30 hari', '30 araw', '30 ngày'],
    ['90 days', '90 天', '90 días', '90 jours', '90 Tage', '90 hari', '90 araw', '90 ngày'],
    ['1 year', '1 年', '1 año', '1 an', '1 Jahr', '1 tahun', '1 taon', '1 năm'],
    // how it works (landing)
    ['CRAWL', '爬取', 'RASTREAR', 'CRAWL', 'CRAWLEN', 'TELUSURI', 'I-CRAWL', 'QUÉT'],
    ['TRACE', '追踪', 'RASTRO', 'TRACER', 'VERFOLGEN', 'LACAK', 'SUNDAN', 'TRUY VẾT'],
    ['LINK', '关联', 'VINCULAR', 'RELIER', 'VERKNÜPFEN', 'HUBUNGKAN', 'IUGNAY', 'LIÊN KẾT'],
    ['VERDICT', '结论', 'VEREDICTO', 'VERDICT', 'URTEIL', 'VONIS', 'HATOL', 'KẾT LUẬN'],
    ['Pulls the top holders straight from Robinhood Chain, no cached API in between. Pools, burn and router addresses are filtered out, so only real wallets are left on the board.',
     '直接从链上拉取头部持有者，中间没有缓存 API。池子、销毁和路由地址会被过滤掉，只留下真实钱包。',
     'Obtiene los principales holders directamente de la cadena, sin API en caché de por medio. Se filtran pools, direcciones de quema y routers, así que solo quedan carteras reales.',
     'Récupère les principaux détenteurs directement depuis la chaîne, sans API en cache entre les deux. Les pools, adresses de burn et routeurs sont filtrés : il ne reste que de vrais portefeuilles.',
     'Holt die größten Holder direkt von der Chain, ohne zwischengespeicherte API. Pools, Burn- und Router-Adressen werden herausgefiltert, übrig bleiben nur echte Wallets.',
     'Mengambil holder teratas langsung dari chain, tanpa API cache di tengahnya. Pool, alamat burn, dan router disaring sehingga hanya dompet asli yang tersisa.',
     'Kinukuha ang mga nangungunang holder direkto mula sa chain, walang naka-cache na API sa gitna. Sinasala ang mga pool, burn at router address kaya totoong wallet lang ang natitira.',
     'Lấy danh sách holder lớn nhất trực tiếp từ chuỗi, không qua API lưu đệm. Pool, địa chỉ đốt và router được lọc bỏ, chỉ còn lại ví thật.'],
    ["Each crawler walks back through a wallet's past before it bought in: how old it is, who funded it, how many transactions it ever made, and whether it hit the first blocks after launch.",
     '每只爬虫回溯钱包买入前的历史：钱包多老、谁为它注资、做过多少笔交易，以及是否在上线后的头几个区块买入。',
     'Cada crawler recorre el pasado de una cartera antes de comprar: su antigüedad, quién la financió, cuántas transacciones hizo y si entró en los primeros bloques tras el lanzamiento.',
     'Chaque crawler remonte le passé d’un portefeuille avant son achat : son âge, qui l’a financé, combien de transactions il a faites et s’il est entré dans les premiers blocs après le lancement.',
     'Jeder Crawler geht die Vergangenheit einer Wallet vor dem Kauf durch: wie alt sie ist, wer sie finanziert hat, wie viele Transaktionen sie je gemacht hat und ob sie in den ersten Blöcken nach dem Start kaufte.',
     'Setiap crawler menelusuri masa lalu dompet sebelum membeli: berapa usianya, siapa yang mendanainya, berapa transaksi yang pernah dibuat, dan apakah masuk di blok pertama setelah peluncuran.',
     'Sinusuri ng bawat crawler ang nakaraan ng wallet bago ito bumili: gaano na ito katagal, sino ang nagpondo, ilang transaksyon na ang nagawa, at kung pumasok ito sa mga unang block pagkatapos ng launch.',
     'Mỗi crawler lần lại quá khứ của ví trước khi mua: ví bao nhiêu tuổi, ai cấp vốn, đã thực hiện bao nhiêu giao dịch và có vào ở những khối đầu tiên sau khi ra mắt không.'],
    ['Connects wallets run by one person: tokens moved between them in one transaction, a shared funder, or a pack of fresh wallets that all bought in the same block.',
     '把同一人操控的钱包连起来：同一笔交易中互转代币、共同的资金方，或在同一区块一起买入的一群新钱包。',
     'Conecta carteras que maneja una sola persona: tokens movidos entre ellas en una transacción, un financiador común o una manada de carteras nuevas que compraron en el mismo bloque.',
     'Relie les portefeuilles tenus par une seule personne : tokens déplacés entre eux dans une transaction, un financeur commun ou une meute de portefeuilles neufs ayant tous acheté dans le même bloc.',
     'Verbindet Wallets einer einzigen Person: Tokens, die in einer Transaktion zwischen ihnen bewegt wurden, ein gemeinsamer Finanzierer oder ein Rudel frischer Wallets, die im selben Block kauften.',
     'Menghubungkan dompet yang dikendalikan satu orang: token yang berpindah di antara mereka dalam satu transaksi, pendana yang sama, atau kawanan dompet baru yang membeli di blok yang sama.',
     'Pinag-uugnay ang mga wallet ng iisang tao: token na inilipat sa pagitan nila sa isang transaksyon, iisang nagpopondo, o grupo ng bagong wallet na sabay bumili sa parehong block.',
     'Nối các ví do một người điều khiển: token chuyển giữa chúng trong một giao dịch, cùng một nguồn vốn, hoặc một bầy ví mới cùng mua trong một khối.'],
    ['Linked wallets collapse into operators, so you see how many real people stand behind the top. Five signals roll into a 0–100 risk score with a one-line reason.',
     '关联钱包合并为操盘方，让你看清头部背后到底有多少真人。五项信号汇总成 0–100 的风险分，并附一句理由。',
     'Las carteras vinculadas se agrupan en operadores, para ver cuántas personas reales hay detrás del top. Cinco señales forman una puntuación de riesgo de 0 a 100 con un motivo de una línea.',
     'Les portefeuilles liés sont regroupés en opérateurs : vous voyez combien de vraies personnes se cachent derrière le top. Cinq signaux donnent un score de risque de 0 à 100 avec une raison en une ligne.',
     'Verknüpfte Wallets werden zu Operatoren zusammengefasst, damit du siehst, wie viele echte Menschen hinter den Top stehen. Fünf Signale ergeben einen Risikoscore von 0–100 mit einer einzeiligen Begründung.',
     'Dompet yang terhubung digabung menjadi operator, jadi Anda tahu berapa orang sungguhan di balik jajaran teratas. Lima sinyal menjadi skor risiko 0–100 dengan alasan satu baris.',
     'Pinagsasama ang mga naka-ugnay na wallet bilang mga operator, para makita mo kung ilang totoong tao ang nasa likod ng top. Limang signal ang bumubuo sa 0–100 na risk score na may isang linyang dahilan.',
     'Các ví liên kết được gộp thành nhà điều hành, để bạn thấy có bao nhiêu người thật đứng sau nhóm đầu. Năm tín hiệu tạo thành điểm rủi ro 0–100 kèm một dòng lý do.'],
    // timeline (landing)
    ['Forked from CrawlScan V1', '源自 CrawlScan V1', 'Fork de CrawlScan V1', 'Fork de CrawlScan V1', 'Fork von CrawlScan V1', 'Fork dari CrawlScan V1', 'Fork mula sa CrawlScan V1', 'Fork từ CrawlScan V1'],
    ['The open-source CrawlScan engine, audited and made private: scans now stop on time, and the draw and rewards modules are gone.',
     '开源的 CrawlScan 引擎，经过审计并私有化：扫描现在按时结束，抽奖和奖励模块已移除。',
     'El motor open source de CrawlScan, auditado y hecho privado: los escaneos ahora terminan a tiempo y se quitaron los módulos de sorteo y recompensas.',
     'Le moteur open source de CrawlScan, audité et rendu privé : les scans s’arrêtent désormais à temps et les modules de tirage et de récompenses ont disparu.',
     'Die Open-Source-Engine von CrawlScan, geprüft und privat gemacht: Scans enden jetzt pünktlich, die Verlosungs- und Belohnungsmodule sind entfernt.',
     'Mesin open-source CrawlScan, diaudit dan dibuat privat: pindaian kini berhenti tepat waktu, modul undian dan hadiah dihapus.',
     'Ang open-source na engine ng CrawlScan, na-audit at ginawang pribado: humihinto na sa oras ang mga scan, at tinanggal ang draw at rewards module.',
     'Bộ máy mã nguồn mở của CrawlScan, đã kiểm toán và chuyển sang riêng tư: lượt quét nay dừng đúng hạn, các mô-đun bốc thăm và thưởng đã bị gỡ.'],
    ['BNB Chain and Base', 'BNB Chain 与 Base', 'BNB Chain y Base', 'BNB Chain et Base', 'BNB Chain und Base', 'BNB Chain dan Base', 'BNB Chain at Base', 'BNB Chain và Base'],
    ['four.meme, Flap and Clanker tokens on BNB Chain and Base, with any 0x address located across every enabled chain.',
     'BNB Chain 和 Base 上的 four.meme、Flap 与 Clanker 代币，任意 0x 地址都会在所有已启用的链上定位。',
     'Tokens de four.meme, Flap y Clanker en BNB Chain y Base; cualquier dirección 0x se localiza en todas las cadenas activas.',
     'Tokens four.meme, Flap et Clanker sur BNB Chain et Base ; toute adresse 0x est localisée sur chaque chaîne activée.',
     'four.meme-, Flap- und Clanker-Tokens auf BNB Chain und Base; jede 0x-Adresse wird auf allen aktivierten Chains gefunden.',
     'Token four.meme, Flap, dan Clanker di BNB Chain dan Base; alamat 0x apa pun dicari di semua chain yang aktif.',
     'Mga token ng four.meme, Flap at Clanker sa BNB Chain at Base; hinahanap ang anumang 0x address sa bawat naka-enable na chain.',
     'Token four.meme, Flap và Clanker trên BNB Chain và Base; mọi địa chỉ 0x đều được tìm trên tất cả các chuỗi đang bật.'],
    ['Uniswap and PancakeSwap launches', 'Uniswap 与 PancakeSwap 上线', 'Lanzamientos en Uniswap y PancakeSwap', 'Lancements Uniswap et PancakeSwap', 'Launches auf Uniswap und PancakeSwap', 'Peluncuran Uniswap dan PancakeSwap', 'Mga launch sa Uniswap at PancakeSwap', 'Ra mắt trên Uniswap và PancakeSwap'],
    ['Tokens launched straight into a Uniswap or PancakeSwap pool are scanned too, on Robinhood, Base and BNB Chain.',
     '直接在 Uniswap 或 PancakeSwap 池子上线的代币同样可扫描，支持 Robinhood、Base 和 BNB Chain。',
     'También se escanean los tokens lanzados directamente en un pool de Uniswap o PancakeSwap, en Robinhood, Base y BNB Chain.',
     'Les tokens lancés directement dans un pool Uniswap ou PancakeSwap sont aussi scannés, sur Robinhood, Base et BNB Chain.',
     'Auch Tokens, die direkt in einen Uniswap- oder PancakeSwap-Pool gestartet wurden, werden gescannt – auf Robinhood, Base und BNB Chain.',
     'Token yang diluncurkan langsung ke pool Uniswap atau PancakeSwap juga dipindai, di Robinhood, Base, dan BNB Chain.',
     'Na-i-scan din ang mga token na direktang inilunsad sa pool ng Uniswap o PancakeSwap, sa Robinhood, Base at BNB Chain.',
     'Token ra mắt thẳng vào pool Uniswap hoặc PancakeSwap cũng được quét, trên Robinhood, Base và BNB Chain.'],
    ['History index and coverage', '历史索引与覆盖度', 'Índice de historial y cobertura', 'Index d’historique et couverture', 'Verlaufsindex und Abdeckung', 'Indeks riwayat dan cakupan', 'History index at saklaw', 'Chỉ mục lịch sử và độ phủ'],
    ['Transfer histories are indexed and resumed between scans, checked against the chain, and every verdict says how complete its data was.',
     '转账历史会被索引并在多次扫描间续读，与链上数据核对，每个结论都会说明其数据的完整程度。',
     'Los historiales de transferencias se indexan y se retoman entre escaneos, se verifican contra la cadena y cada veredicto indica qué tan completos eran sus datos.',
     'Les historiques de transferts sont indexés et repris entre les scans, vérifiés sur la chaîne, et chaque verdict indique à quel point ses données étaient complètes.',
     'Transfer-Historien werden indexiert und zwischen Scans fortgesetzt, gegen die Chain geprüft, und jedes Urteil sagt, wie vollständig seine Daten waren.',
     'Riwayat transfer diindeks dan dilanjutkan antar pindaian, dicek dengan chain, dan setiap vonis menyebut seberapa lengkap datanya.',
     'Ini-index at ipinagpapatuloy ang kasaysayan ng transfer sa pagitan ng mga scan, sinusuri laban sa chain, at sinasabi ng bawat hatol kung gaano kakumpleto ang data nito.',
     'Lịch sử chuyển khoản được lập chỉ mục và nối tiếp giữa các lượt quét, đối chiếu với chuỗi, và mỗi kết luận đều cho biết dữ liệu đầy đủ đến đâu.'],
    ['One scanner for Robinhood, Solana, Base and BNB Chain memecoins, crawled by peacock spiders.',
     '一个扫描器覆盖 Robinhood、Solana、Base 与 BNB Chain 的 Meme 币，由孔雀蜘蛛爬取。',
     'Un solo escáner para memecoins de Robinhood, Solana, Base y BNB Chain, rastreados por arañas pavo real.',
     'Un seul scanner pour les memecoins Robinhood, Solana, Base et BNB Chain, parcourus par des araignées paons.',
     'Ein Scanner für Memecoins auf Robinhood, Solana, Base und BNB Chain, durchkrabbelt von Pfauenspinnen.',
     'Satu pemindai untuk memecoin Robinhood, Solana, Base, dan BNB Chain, ditelusuri oleh laba-laba merak.',
     'Iisang scanner para sa mga memecoin ng Robinhood, Solana, Base at BNB Chain, kinacrawl ng mga peacock spider.',
     'Một trình quét cho memecoin trên Robinhood, Solana, Base và BNB Chain, do những chú nhện công thu thập.'],
    ['Every holder', '每一位持有者', 'Todos los holders', 'Tous les détenteurs', 'Jeder Holder', 'Setiap holder', 'Bawat holder', 'Mọi holder'],
    ["Every scan lists all of a token's holders, not just the top 20, with the biggest ones examined in depth.",
     '每次扫描都会列出代币的全部持有者，而不只是前 20 名，并深入检查最大的那些。',
     'Cada escaneo lista todos los holders del token, no solo los 20 principales, y examina a fondo los más grandes.',
     'Chaque scan liste tous les détenteurs du token, pas seulement les 20 premiers, et examine en détail les plus gros.',
     'Jeder Scan listet alle Holder eines Tokens auf, nicht nur die Top 20, und prüft die größten im Detail.',
     'Setiap pindaian mencantumkan semua holder token, bukan hanya 20 teratas, dan memeriksa yang terbesar secara mendalam.',
     'Nililista ng bawat scan ang lahat ng holder ng token, hindi lang ang top 20, at sinusuri nang malalim ang pinakamalalaki.',
     'Mỗi lượt quét liệt kê toàn bộ holder của token, không chỉ top 20, và phân tích sâu những holder lớn nhất.'],
    ['Live event stream', '实时事件流', 'Flujo de eventos en vivo', 'Flux d’événements en direct', 'Live-Ereignisstrom', 'Aliran peristiwa langsung', 'Live na daloy ng event', 'Luồng sự kiện trực tiếp'],
    ["The crawlers' steps reach the page the moment they happen, from the first second of the scan.",
     '从扫描的第一秒起，爬虫的每一步都会即时显示在页面上。',
     'Los pasos de los crawlers llegan a la página en el momento en que ocurren, desde el primer segundo del escaneo.',
     'Les étapes des crawlers arrivent sur la page au moment où elles se produisent, dès la première seconde du scan.',
     'Die Schritte der Crawler erscheinen auf der Seite, sobald sie passieren – ab der ersten Sekunde des Scans.',
     'Langkah para crawler tampil di halaman saat itu juga, sejak detik pertama pindaian.',
     'Lumalabas sa page ang mga hakbang ng crawler sa sandaling mangyari ito, mula sa unang segundo ng scan.',
     'Từng bước của crawler hiện lên trang ngay khi xảy ra, từ giây đầu tiên của lượt quét.'],
    ['Wallet forensics', '钱包取证', 'Análisis forense de carteras', 'Analyse des portefeuilles', 'Wallet-Forensik', 'Forensik dompet', 'Forensics ng wallet', 'Pháp chứng ví'],
    ["Every top holder's trades, P&L at market prices, time held, and who funded the wallet — with shared funders called out.",
     '每个头部持有者的交易、按市价计算的盈亏、持有时长，以及谁为钱包注资——共同资金方会被标出。',
     'Las operaciones de cada holder principal, el P&L a precios de mercado, el tiempo de tenencia y quién financió la cartera, señalando los financiadores comunes.',
     'Les transactions de chaque gros détenteur, le P&L au prix du marché, la durée de détention et qui a financé le portefeuille — financeurs communs signalés.',
     'Trades jedes Top-Holders, G&V zu Marktpreisen, Haltedauer und wer die Wallet finanziert hat – gemeinsame Finanzierer werden hervorgehoben.',
     'Transaksi setiap holder teratas, P&L dengan harga pasar, lama memegang, dan siapa yang mendanai dompet — pendana yang sama ditandai.',
     'Mga trade ng bawat nangungunang holder, P&L sa presyo ng merkado, tagal ng paghawak, at kung sino ang nagpondo ng wallet — tinutukoy ang iisang nagpopondo.',
     'Giao dịch của từng holder lớn, lãi/lỗ theo giá thị trường, thời gian nắm giữ và ai cấp vốn cho ví — nêu rõ các nguồn vốn chung.'],
    ['Relationship graph', '关系图', 'Grafo de relaciones', 'Graphe des relations', 'Beziehungsgraph', 'Grafik hubungan', 'Graph ng ugnayan', 'Đồ thị quan hệ'],
    ['Funding links between holders, with exchange-like hubs told apart, drawn as a live graph of who is tied to whom.',
     '持有者之间的资金关联，区分出类似交易所的枢纽，绘制成一张谁与谁相连的实时图。',
     'Vínculos de financiación entre holders, distinguiendo los hubs tipo exchange, dibujados como un grafo en vivo de quién está ligado a quién.',
     'Liens de financement entre détenteurs, en distinguant les hubs de type exchange, dessinés en graphe en direct de qui est lié à qui.',
     'Finanzierungsverbindungen zwischen Holdern, börsenähnliche Hubs unterschieden, als Live-Graph, wer mit wem verbunden ist.',
     'Tautan pendanaan antar holder, dengan hub mirip bursa dibedakan, digambar sebagai grafik langsung siapa terhubung dengan siapa.',
     'Mga ugnay ng pondo sa pagitan ng mga holder, hiwalay ang mga hub na parang exchange, iginuhit bilang live graph kung sino ang konektado kanino.',
     'Liên kết cấp vốn giữa các holder, tách riêng các trung tâm kiểu sàn, vẽ thành đồ thị trực tiếp ai gắn với ai.'],
    ['Memory and cabals', '记忆与团伙', 'Memoria y cábalas', 'Mémoire et cabales', 'Gedächtnis und Kabalen', 'Memori dan komplotan', 'Memorya at mga kabal', 'Bộ nhớ và băng nhóm'],
    ['Every scan is remembered: holders that keep showing up together across tokens, repeat funders and serial deployers are called out.',
     '每次扫描都会被记住：在多个代币中反复一起出现的持有者、重复资金方和连续部署者都会被标出。',
     'Cada escaneo se recuerda: se señalan los holders que aparecen juntos en varios tokens, los financiadores repetidos y los desplegadores en serie.',
     'Chaque scan est mémorisé : les détenteurs qui reviennent ensemble sur plusieurs tokens, les financeurs récurrents et les déployeurs en série sont signalés.',
     'Jeder Scan wird gespeichert: Holder, die über Tokens hinweg immer wieder zusammen auftauchen, wiederkehrende Finanzierer und Serien-Deployer werden hervorgehoben.',
     'Setiap pindaian diingat: holder yang terus muncul bersama di berbagai token, pendana berulang, dan deployer berantai ditandai.',
     'Natatandaan ang bawat scan: tinutukoy ang mga holder na paulit-ulit na magkasama sa iba’t ibang token, paulit-ulit na nagpopondo at serial deployer.',
     'Mọi lượt quét đều được ghi nhớ: holder liên tục xuất hiện cùng nhau ở nhiều token, nguồn vốn lặp lại và người triển khai hàng loạt đều được nêu ra.'],
    ['Versioned scoring', '版本化评分', 'Puntuación versionada', 'Score versionné', 'Versioniertes Scoring', 'Penilaian berversi', 'Bersyon na pag-iskor', 'Chấm điểm có phiên bản'],
    ['Every verdict says how sure it is, and a shadow model scores the new memory evidence beside the live one until outcomes prove it.',
     '每个结论都会说明其可信度；影子模型与实时模型并行评分新的记忆证据，直到结果证明它。',
     'Cada veredicto dice qué tan seguro es, y un modelo en la sombra puntúa la nueva evidencia de memoria junto al modelo en vivo hasta que los resultados lo prueben.',
     'Chaque verdict indique son degré de certitude, et un modèle fantôme note les nouvelles preuves de mémoire à côté du modèle en direct jusqu’à ce que les résultats le valident.',
     'Jedes Urteil sagt, wie sicher es ist, und ein Schattenmodell bewertet die neuen Gedächtnis-Belege neben dem Live-Modell, bis Ergebnisse es bestätigen.',
     'Setiap vonis menyebut seberapa yakin, dan model bayangan menilai bukti memori baru di samping model langsung sampai hasil membuktikannya.',
     'Sinasabi ng bawat hatol kung gaano ito katiyak, at isang shadow model ang nag-i-iskor sa bagong ebidensya ng memorya kasabay ng live hanggang patunayan ng mga resulta.',
     'Mỗi kết luận đều cho biết mức chắc chắn, và một mô hình bóng chấm điểm bằng chứng bộ nhớ mới song song với mô hình trực tiếp cho tới khi kết quả chứng minh.'],
    ['Launch Radar', '上线雷达', 'Radar de lanzamientos', 'Radar des lancements', 'Launch-Radar', 'Radar peluncuran', 'Launch Radar', 'Radar ra mắt'],
    ['Rug and Gem Replay', '跑路与宝石回放', 'Repetición de rugs y gemas', 'Replay des rugs et pépites', 'Rug- und Gem-Replay', 'Replay rug dan permata', 'Rug at Gem Replay', 'Xem lại rug và gem'],
    ['Read the post on X ↗', '在 X 上阅读 ↗', 'Leer la publicación en X ↗', 'Lire le post sur X ↗', 'Beitrag auf X lesen ↗', 'Baca postingan di X ↗', 'Basahin ang post sa X ↗', 'Đọc bài trên X ↗'],
    ['network', '网络', 'red', 'réseau', 'Netzwerk', 'jaringan', 'network', 'mạng'],
    // result: criteria table, live log, graph, forensics, model
    ['What the crawlers checked', '爬虫检查了什么', 'Lo que revisaron los crawlers', 'Ce que les crawlers ont vérifié', 'Was die Crawler geprüft haben', 'Yang diperiksa crawler', 'Ang sinuri ng mga crawler', 'Những gì crawler đã kiểm tra'],
    ['criterion', '检查项', 'criterio', 'critère', 'Kriterium', 'kriteria', 'pamantayan', 'tiêu chí'],
    ['finding', '发现', 'hallazgo', 'constat', 'Befund', 'temuan', 'natuklasan', 'phát hiện'],
    ['Operator clustering', '操盘方聚类', 'Agrupación de operadores', 'Regroupement des opérateurs', 'Operator-Clustering', 'Pengelompokan operator', 'Pag-grupo ng operator', 'Gom cụm nhà điều hành'],
    ['Linked wallets collapse into one operator, scored by dump impact', '关联钱包合并为一个操盘方，按抛售冲击评分', 'Las carteras vinculadas se agrupan en un operador, puntuado por su impacto de venta', 'Les portefeuilles liés sont regroupés en un opérateur, noté selon son impact de vente', 'Verknüpfte Wallets werden zu einem Operator, bewertet nach Verkaufswirkung', 'Dompet terhubung digabung jadi satu operator, dinilai dari dampak jual', 'Pinagsasama ang mga naka-ugnay na wallet sa isang operator, ini-iskor ayon sa epekto ng pagbenta', 'Các ví liên kết gộp thành một nhà điều hành, chấm theo tác động xả'],
    ['Pack detection', '团伙检测', 'Detección de manadas', 'Détection de meutes', 'Rudel-Erkennung', 'Deteksi kawanan', 'Pagtukoy ng pangkat', 'Phát hiện bầy'],
    ['3+ fresh wallets buying in one block with matching sizes', '3 个以上新钱包在同一区块以相近金额买入', '3+ carteras nuevas comprando en un bloque con tamaños similares', '3+ portefeuilles neufs achetant dans un même bloc avec des montants similaires', '3+ frische Wallets, die in einem Block mit ähnlichen Beträgen kaufen', '3+ dompet baru membeli di satu blok dengan ukuran serupa', '3+ bagong wallet na bumili sa isang block na may magkakatugmang laki', '3+ ví mới mua trong một khối với khối lượng tương tự'],
    ['Proven links', '已证实关联', 'Vínculos probados', 'Liens prouvés', 'Bewiesene Verbindungen', 'Tautan terbukti', 'Napatunayang ugnay', 'Liên kết đã chứng minh'],
    ['Direct on-chain tie: tokens moved between two holders', '直接链上关联：代币在两个持有者之间转移', 'Vínculo on-chain directo: tokens movidos entre dos holders', 'Lien on-chain direct : tokens déplacés entre deux détenteurs', 'Direkte On-Chain-Verbindung: Tokens zwischen zwei Holdern bewegt', 'Kaitan on-chain langsung: token berpindah antara dua holder', 'Direktang ugnay sa chain: token na inilipat sa pagitan ng dalawang holder', 'Liên kết on-chain trực tiếp: token chuyển giữa hai holder'],
    ['Bundle detection', '捆绑检测', 'Detección de bundles', 'Détection de bundles', 'Bundle-Erkennung', 'Deteksi bundle', 'Pagtukoy ng bundle', 'Phát hiện bundle'],
    ['Wallets that received supply instead of buying it', '通过接收而非买入获得筹码的钱包', 'Carteras que recibieron suministro en lugar de comprarlo', 'Portefeuilles ayant reçu des tokens au lieu de les acheter', 'Wallets, die Tokens erhalten statt gekauft haben', 'Dompet yang menerima suplai alih-alih membelinya', 'Mga wallet na tumanggap ng supply sa halip na bumili', 'Ví nhận token thay vì mua'],
    ['Virgin wallets', '新号钱包', 'Carteras vírgenes', 'Portefeuilles vierges', 'Jungfräuliche Wallets', 'Dompet perawan', 'Mga bagong wallet', 'Ví trắng'],
    ['Never traded a token before this one', '在此之前从未交易过任何代币', 'Nunca operaron un token antes de este', 'N’ont jamais échangé de token avant celui-ci', 'Vor diesem Token nie einen Token gehandelt', 'Belum pernah memperdagangkan token sebelum ini', 'Hindi pa nakapag-trade ng token bago ito', 'Chưa từng giao dịch token nào trước token này'],
    ['Sniper tracking', '狙击追踪', 'Seguimiento de snipers', 'Suivi des snipers', 'Sniper-Verfolgung', 'Pelacakan sniper', 'Pagsubaybay sa sniper', 'Theo dõi sniper'],
    ['Entries in the first 30 seconds after launch', '上线后前 30 秒内的买入', 'Entradas en los primeros 30 segundos tras el lanzamiento', 'Entrées dans les 30 premières secondes après le lancement', 'Einstiege in den ersten 30 Sekunden nach dem Start', 'Masuk dalam 30 detik pertama setelah peluncuran', 'Mga pasok sa unang 30 segundo pagkatapos ng launch', 'Lệnh vào trong 30 giây đầu sau khi ra mắt'],
    ['Concentration', '集中度', 'Concentración', 'Concentration', 'Konzentration', 'Konsentrasi', 'Konsentrasyon', 'Mức tập trung'],
    ['Share of real circulating float held by the top', '头部持有者所占真实流通量比例', 'Parte del float circulante real en manos del top', 'Part du flottant réel détenue par le top', 'Anteil des echten Umlaufs bei den Top-Holdern', 'Porsi float beredar sebenarnya yang dipegang teratas', 'Bahagi ng tunay na umiikot na float na hawak ng top', 'Tỷ lệ lưu hành thật do nhóm đầu nắm'],
    ['Deployer', '部署者', 'Desplegador', 'Déployeur', 'Deployer', 'Deployer', 'Deployer', 'Người triển khai'],
    ['Creator wallet still among the top holders', '创建者钱包仍在头部持有者中', 'La cartera creadora sigue entre los principales holders', 'Le portefeuille créateur est toujours parmi les principaux détenteurs', 'Ersteller-Wallet ist noch unter den Top-Holdern', 'Dompet pembuat masih di antara holder teratas', 'Nasa nangungunang holder pa rin ang wallet ng gumawa', 'Ví người tạo vẫn nằm trong nhóm holder lớn'],
    ['no packs', '无团伙', 'sin manadas', 'aucune meute', 'keine Rudel', 'tidak ada kawanan', 'walang pangkat', 'không có bầy'],
    ['none', '无', 'ninguno', 'aucun', 'keine', 'tidak ada', 'wala', 'không có'],
    ['not in top holders', '不在头部持有者中', 'no está entre los principales', 'absent du top', 'nicht unter den Top-Holdern', 'tidak di holder teratas', 'wala sa nangungunang holder', 'không trong nhóm holder lớn'],
    ['pass', '通过', 'ok', 'ok', 'ok', 'lolos', 'pasado', 'đạt'],
    ['watch', '关注', 'vigilar', 'à surveiller', 'beobachten', 'pantau', 'bantayan', 'theo dõi'],
    ['high', '高', 'alto', 'élevé', 'hoch', 'tinggi', 'mataas', 'cao'],
    ['info', '信息', 'info', 'info', 'Info', 'info', 'info', 'thông tin'],
    ['→ operator', '→ 操盘方', '→ operador', '→ opérateur', '→ Operator', '→ operator', '→ operator', '→ nhà điều hành'],
    ['resolving token', '正在解析代币', 'resolviendo el token', 'résolution du token', 'Token wird aufgelöst', 'mengenali token', 'tinutukoy ang token', 'đang xác định token'],
    ['reading top holders', '正在读取头部持有者', 'leyendo los principales holders', 'lecture des principaux détenteurs', 'Top-Holder werden gelesen', 'membaca holder teratas', 'binabasa ang mga nangungunang holder', 'đang đọc holder lớn'],
    ['classifying entries', '正在分类买入', 'clasificando entradas', 'classement des entrées', 'Einstiege werden eingeordnet', 'mengklasifikasi entri', 'inuuri ang mga pasok', 'đang phân loại lệnh vào'],
    ['tracing wallet history', '正在追溯钱包历史', 'rastreando el historial de carteras', 'traçage de l’historique des portefeuilles', 'Wallet-Historie wird verfolgt', 'menelusuri riwayat dompet', 'sinusundan ang kasaysayan ng wallet', 'đang truy lịch sử ví'],
    ['all holders', '全部持有者', 'todos los holders', 'tous les détenteurs', 'alle Holder', 'semua holder', 'lahat ng holder', 'tất cả holder'],
    ['analysed', '已分析', 'analizado', 'analysé', 'analysiert', 'dianalisis', 'nasuri', 'đã phân tích'],
    ['show 100 more', '再显示 100 个', 'mostrar 100 más', 'afficher 100 de plus', '100 weitere anzeigen', 'tampilkan 100 lagi', 'magpakita ng 100 pa', 'xem thêm 100'],
    ['holder list unavailable', '持有者列表不可用', 'lista de holders no disponible', 'liste des détenteurs indisponible', 'Holder-Liste nicht verfügbar', 'daftar holder tidak tersedia', 'hindi available ang listahan ng holder', 'không có danh sách holder'],
    ['loading…', '加载中…', 'cargando…', 'chargement…', 'lädt…', 'memuat…', 'naglo-load…', 'đang tải…'],
    ['holder (size = share; colour = merged operator)', '持有者（大小 = 占比；颜色 = 合并后的操盘方）', 'holder (tamaño = participación; color = operador agrupado)', 'détenteur (taille = part ; couleur = opérateur regroupé)', 'Holder (Größe = Anteil; Farbe = zusammengefasster Operator)', 'holder (ukuran = porsi; warna = operator gabungan)', 'holder (laki = bahagi; kulay = pinagsamang operator)', 'holder (kích thước = tỷ lệ; màu = nhà điều hành đã gộp)'],
    ['funder linking its holders', '关联其持有者的资金方', 'financiador que vincula a sus holders', 'financeur reliant ses détenteurs', 'Finanzierer, der seine Holder verbindet', 'pendana yang menghubungkan holder-nya', 'nagpopondo na nag-uugnay sa kanyang mga holder', 'nguồn vốn nối các holder của nó'],
    ['hub funder (exchange-like): shown, never links', '枢纽资金方（类似交易所）：显示但不关联', 'financiador hub (tipo exchange): se muestra, nunca vincula', 'financeur hub (type exchange) : affiché, ne relie jamais', 'Hub-Finanzierer (börsenähnlich): angezeigt, verbindet nie', 'pendana hub (mirip bursa): ditampilkan, tidak pernah menghubungkan', 'hub na nagpopondo (parang exchange): ipinapakita, hindi nag-uugnay', 'nguồn vốn trung tâm (kiểu sàn): hiển thị, không bao giờ nối'],
    ['No links between the top holders: every holder counts as its own operator.', '头部持有者之间没有关联：每个持有者都算作独立的操盘方。', 'No hay vínculos entre los principales holders: cada holder cuenta como su propio operador.', 'Aucun lien entre les principaux détenteurs : chaque détenteur compte comme son propre opérateur.', 'Keine Verbindungen zwischen den Top-Holdern: Jeder Holder zählt als eigener Operator.', 'Tidak ada tautan antar holder teratas: setiap holder dihitung sebagai operator sendiri.', 'Walang ugnay sa pagitan ng mga nangungunang holder: bawat holder ay sariling operator.', 'Không có liên kết giữa các holder lớn: mỗi holder được tính là một nhà điều hành riêng.'],
    ['USD at candle prices · estimates', '按 K 线价格计算的美元 · 估算值', 'USD a precios de vela · estimaciones', 'USD aux prix des bougies · estimations', 'USD zu Kerzenpreisen · Schätzungen', 'USD dengan harga candle · perkiraan', 'USD sa presyo ng candle · tantya', 'USD theo giá nến · ước tính'],
    ['hub', '枢纽', 'hub', 'hub', 'Hub', 'hub', 'hub', 'trung tâm'],
    ['p&l', '盈亏', 'p&l', 'p&l', 'G&V', 'p&l', 'p&l', 'lãi/lỗ'],
    ['off', '关闭', 'desactivado', 'désactivé', 'aus', 'nonaktif', 'naka-off', 'tắt'],
    ['full history · verified against the chain', '完整历史 · 已与链上核对', 'historial completo · verificado contra la cadena', 'historique complet · vérifié sur la chaîne', 'vollständige Historie · gegen die Chain geprüft', 'riwayat lengkap · dicek dengan chain', 'buong kasaysayan · sinuri laban sa chain', 'lịch sử đầy đủ · đã đối chiếu với chuỗi'],
    ['The live model gives the verdict. Shadow models are scored on the same evidence and kept in memory, so they can be checked against what tokens actually did before one replaces it.',
     '结论由实时模型给出。影子模型基于相同证据评分并存入记忆，在替换实时模型之前可以用代币的实际表现检验它们。',
     'El veredicto lo da el modelo en vivo. Los modelos en la sombra se puntúan con la misma evidencia y se guardan en memoria, para comprobarlos contra lo que hicieron los tokens antes de que uno lo reemplace.',
     'Le verdict vient du modèle en direct. Les modèles fantômes sont notés sur les mêmes preuves et gardés en mémoire, pour être confrontés à ce que les tokens ont réellement fait avant qu’un d’eux ne le remplace.',
     'Das Urteil kommt vom Live-Modell. Schattenmodelle werden mit denselben Belegen bewertet und gespeichert, damit sie am tatsächlichen Verlauf der Tokens geprüft werden können, bevor eines es ersetzt.',
     'Vonis diberikan model langsung. Model bayangan dinilai dengan bukti yang sama dan disimpan di memori, agar bisa dicek terhadap apa yang benar-benar terjadi sebelum salah satunya menggantikannya.',
     'Ang live model ang nagbibigay ng hatol. Ini-iskor ang mga shadow model sa parehong ebidensya at itinatabi sa memorya, para masuri laban sa tunay na nangyari sa mga token bago may pumalit dito.',
     'Kết luận do mô hình trực tiếp đưa ra. Các mô hình bóng được chấm trên cùng bằng chứng và lưu trong bộ nhớ, để đối chiếu với diễn biến thật của token trước khi một mô hình thay thế nó.'],
    ['and', '和', 'y', 'et', 'und', 'dan', 'at', 'và'],
    ['probably', '可能', 'probablemente', 'probablement', 'wahrscheinlich', 'kemungkinan', 'malamang', 'có thể'],
    ['sell of float', '流通量卖出比例', 'venta del float', 'vente du flottant', 'Verkauf des Floats', 'jual float', 'benta ng float', 'bán lưu hành'],
    ['relationship graph of the top holders', '头部持有者关系图', 'grafo de relaciones de los principales holders', 'graphe des relations des principaux détenteurs', 'Beziehungsgraph der Top-Holder', 'grafik hubungan holder teratas', 'graph ng ugnayan ng mga nangungunang holder', 'đồ thị quan hệ của các holder lớn'],
    ['max operator % float', '操盘方最多占流通量 %', 'operador máx. % del float', 'opérateur max % du flottant', 'Operator max. % des Floats', 'operator maks % float', 'max na operator % ng float', 'nhà điều hành tối đa % lưu hành'],
    ['min change since scan %', '扫描以来最小变化 %', 'cambio mín. desde el escaneo %', 'variation min depuis le scan %', 'Mindeständerung seit dem Scan %', 'perubahan min sejak pindaian %', 'min na pagbabago mula sa scan %', 'thay đổi tối thiểu từ lúc quét %'],
    ['full coverage only', '仅完整覆盖', 'solo cobertura completa', 'couverture complète uniquement', 'nur vollständige Abdeckung', 'hanya cakupan penuh', 'buong saklaw lang', 'chỉ độ phủ đầy đủ'],
    ['Fresh tokens whose latest real CrawlMeme scan meets your distribution and risk filters. Each row shows when it was scanned. Tokens drop off or are downgraded when the verdict slips, operators or the launch bundle sell, the market turns against the scan, the token collapses, or the data goes stale.',
     '最新一次真实 CrawlMeme 扫描符合你的分布与风险筛选条件的新代币。每行显示扫描时间。当结论变差、操盘方或上线捆绑卖出、市场走势与扫描相反、代币崩盘或数据过期时，代币会被降级或移出。',
     'Tokens nuevos cuyo último escaneo real de CrawlMeme cumple tus filtros de distribución y riesgo. Cada fila muestra cuándo se escaneó. Los tokens se degradan o salen cuando el veredicto empeora, venden los operadores o el bundle de lanzamiento, el mercado va en contra del escaneo, el token colapsa o los datos quedan desactualizados.',
     'Tokens récents dont le dernier vrai scan CrawlMeme passe vos filtres de distribution et de risque. Chaque ligne indique quand il a été scanné. Les tokens sont rétrogradés ou retirés quand le verdict se dégrade, que les opérateurs ou le bundle de lancement vendent, que le marché contredit le scan, que le token s’effondre ou que les données deviennent périmées.',
     'Frische Tokens, deren letzter echter CrawlMeme-Scan deine Verteilungs- und Risikofilter erfüllt. Jede Zeile zeigt, wann gescannt wurde. Tokens werden herabgestuft oder entfernt, wenn das Urteil schlechter wird, Operatoren oder das Launch-Bundle verkaufen, der Markt gegen den Scan läuft, der Token einbricht oder die Daten veralten.',
     'Token baru yang pindaian CrawlMeme asli terakhirnya memenuhi filter distribusi dan risiko Anda. Setiap baris menunjukkan kapan dipindai. Token diturunkan atau dihapus jika vonis memburuk, operator atau bundle peluncuran menjual, pasar berlawanan dengan pindaian, token ambruk, atau data menjadi usang.',
     'Mga bagong token na pumapasa ang pinakabagong totoong scan ng CrawlMeme sa iyong mga filter sa distribusyon at panganib. Ipinapakita ng bawat row kung kailan ito na-scan. Ibinababa o tinatanggal ang token kapag lumala ang hatol, nagbenta ang mga operator o ang launch bundle, gumalaw ang merkado laban sa scan, bumagsak ang token, o naluma ang data.',
     'Token mới có lượt quét CrawlMeme thật gần nhất đạt bộ lọc phân bổ và rủi ro của bạn. Mỗi dòng cho biết thời điểm quét. Token bị hạ hoặc loại khi kết luận xấu đi, nhà điều hành hoặc gói ra mắt bán ra, thị trường đi ngược kết quả quét, token sụp đổ hoặc dữ liệu đã cũ.'],
    ["Data: CrawlMeme's own scans, stored in its memory. New launches are discovered and scanned every 30 minutes, and you can rescan any token here. Prices: GeckoTerminal, then DexScreener, then DexPaprika. Bundle = launch supply still concentrated in launch wallets (% of supply); operator = largest linked group (% of float). This is not financial advice: a clean scan describes the holders at scan time, not the future.",
     '数据：CrawlMeme 自己的扫描，存于其记忆库。每 30 分钟发现并扫描新上线代币，你也可以在这里重新扫描任意代币。价格：GeckoTerminal，其次 DexScreener，再次 DexPaprika。捆绑 = 仍集中在上线钱包中的上线筹码（占总量 %）；操盘方 = 最大的关联群体（占流通量 %）。这不是投资建议：干净的扫描描述的是扫描时的持有者，而非未来。',
     'Datos: los escaneos propios de CrawlMeme, guardados en su memoria. Cada 30 minutos se descubren y escanean nuevos lanzamientos, y aquí puedes reescanear cualquier token. Precios: GeckoTerminal, luego DexScreener, luego DexPaprika. Bundle = suministro de lanzamiento aún concentrado en carteras de lanzamiento (% del suministro); operador = mayor grupo vinculado (% del float). No es asesoramiento financiero: un escaneo limpio describe a los holders en el momento del escaneo, no el futuro.',
     'Données : les scans de CrawlMeme, stockés dans sa mémoire. Les nouveaux lancements sont découverts et scannés toutes les 30 minutes, et vous pouvez rescanner n’importe quel token ici. Prix : GeckoTerminal, puis DexScreener, puis DexPaprika. Bundle = offre de lancement encore concentrée dans les portefeuilles de lancement (% de l’offre) ; opérateur = plus grand groupe lié (% du flottant). Ce n’est pas un conseil financier : un scan propre décrit les détenteurs au moment du scan, pas l’avenir.',
     'Daten: CrawlMemes eigene Scans aus seinem Gedächtnis. Neue Launches werden alle 30 Minuten entdeckt und gescannt, und hier kannst du jeden Token neu scannen. Preise: GeckoTerminal, dann DexScreener, dann DexPaprika. Bundle = Launch-Menge, die noch in Launch-Wallets konzentriert ist (% der Menge); Operator = größte verknüpfte Gruppe (% des Floats). Keine Finanzberatung: Ein sauberer Scan beschreibt die Holder zum Scanzeitpunkt, nicht die Zukunft.',
     'Data: pindaian CrawlMeme sendiri, disimpan di memorinya. Peluncuran baru ditemukan dan dipindai setiap 30 menit, dan Anda bisa memindai ulang token apa pun di sini. Harga: GeckoTerminal, lalu DexScreener, lalu DexPaprika. Bundle = suplai peluncuran yang masih terkonsentrasi di dompet peluncuran (% suplai); operator = grup terhubung terbesar (% float). Bukan nasihat keuangan: pindaian bersih menggambarkan holder saat dipindai, bukan masa depan.',
     'Data: sariling mga scan ng CrawlMeme, nakaimbak sa memorya nito. Tinutuklas at sina-scan ang mga bagong launch tuwing 30 minuto, at maaari mong i-scan muli ang anumang token dito. Presyo: GeckoTerminal, saka DexScreener, saka DexPaprika. Bundle = launch supply na nakakonsentra pa sa mga launch wallet (% ng supply); operator = pinakamalaking naka-ugnay na grupo (% ng float). Hindi ito payong pinansyal: inilalarawan ng malinis na scan ang mga holder sa oras ng scan, hindi ang hinaharap.',
     'Dữ liệu: các lượt quét của chính CrawlMeme, lưu trong bộ nhớ. Token mới được phát hiện và quét mỗi 30 phút, và bạn có thể quét lại bất kỳ token nào tại đây. Giá: GeckoTerminal, rồi DexScreener, rồi DexPaprika. Gói = nguồn cung ra mắt vẫn tập trung trong ví ra mắt (% tổng cung); nhà điều hành = nhóm liên kết lớn nhất (% lưu hành). Không phải lời khuyên tài chính: một lượt quét sạch mô tả holder tại thời điểm quét, không phải tương lai.'],
    ['Historical results describe what happened to tokens CrawlMeme already scanned, measured with public market data. They are not a promise about any future token: a past hit rate is not predictive certainty, and small samples swing widely (see the 95% ranges).',
     '历史结果描述的是 CrawlMeme 已扫描代币后来发生了什么，用公开市场数据衡量。它们不是对任何未来代币的承诺：过去的命中率不代表预测的确定性，小样本波动很大（见 95% 区间）。',
     'Los resultados históricos describen qué pasó con los tokens que CrawlMeme ya escaneó, medido con datos públicos de mercado. No son una promesa sobre ningún token futuro: un acierto pasado no es certeza predictiva y las muestras pequeñas varían mucho (ver los rangos del 95%).',
     'Les résultats historiques décrivent ce qui est arrivé aux tokens déjà scannés par CrawlMeme, mesuré avec des données de marché publiques. Ce n’est pas une promesse pour un token futur : un taux de réussite passé n’est pas une certitude, et les petits échantillons varient beaucoup (voir les intervalles à 95 %).',
     'Historische Ergebnisse beschreiben, was mit bereits von CrawlMeme gescannten Tokens geschah, gemessen mit öffentlichen Marktdaten. Sie sind kein Versprechen für künftige Tokens: Eine frühere Trefferquote ist keine Vorhersagesicherheit, und kleine Stichproben schwanken stark (siehe die 95-%-Bereiche).',
     'Hasil historis menggambarkan apa yang terjadi pada token yang sudah dipindai CrawlMeme, diukur dengan data pasar publik. Ini bukan janji untuk token mendatang: tingkat keberhasilan masa lalu bukan kepastian prediksi, dan sampel kecil sangat berfluktuasi (lihat rentang 95%).',
     'Inilalarawan ng mga makasaysayang resulta ang nangyari sa mga token na na-scan na ng CrawlMeme, sinukat gamit ang pampublikong market data. Hindi ito pangako tungkol sa anumang token sa hinaharap: hindi katiyakan ng hula ang nakaraang hit rate, at malaki ang galaw ng maliliit na sample (tingnan ang 95% na saklaw).',
     'Kết quả lịch sử mô tả điều đã xảy ra với các token CrawlMeme đã quét, đo bằng dữ liệu thị trường công khai. Đây không phải lời hứa cho bất kỳ token tương lai nào: tỷ lệ trúng trong quá khứ không phải sự chắc chắn dự báo, và mẫu nhỏ dao động rất mạnh (xem khoảng 95%).'],
    ['Method: each eligible scan is a token\'s first scan with a verdict and a market price, so a token scanned many times counts once. Records are append-only: a scan row, its market at scan time and every later market snapshot can never be edited, and each scan carries a SHA-256 fingerprint ("record" re-checks it). A tracker samples every scanned token\'s market every ~30 minutes for 7 days (DexScreener), so peaks and troughs are what was observed at those times; moves between samples can be missed. An event counts as soon as it is seen; a "no event" needs the whole window to pass with a snapshot in its last quarter, otherwise the case stays unresolved and is not counted either way. Precision = true positives / flagged; recall = true positives / events; coverage = resolved / eligible. Ranges are 95% Wilson intervals.',
     '方法：每个符合条件的扫描是代币首次带有结论和市场价格的扫描，因此多次扫描的代币只计一次。记录只追加：扫描行、扫描时的市场以及之后每个市场快照都无法修改，每次扫描都带有 SHA-256 指纹（“记录”会重新核验）。追踪器在 7 天内每约 30 分钟采样一次每个已扫描代币的市场（DexScreener），因此峰值和谷值是这些时刻观察到的；采样之间的波动可能被漏掉。事件一经观察即计入；“未发生”需要整个窗口结束且最后四分之一内有快照，否则该案例保持未判定，不计入任何一方。精确率 = 真阳性 / 已标记；召回率 = 真阳性 / 事件；覆盖度 = 已判定 / 符合条件。区间为 95% Wilson 区间。',
     'Método: cada escaneo elegible es el primer escaneo de un token con veredicto y precio de mercado, así que un token escaneado muchas veces cuenta una vez. Los registros solo se añaden: una fila de escaneo, su mercado al escanear y cada instantánea posterior nunca se pueden editar, y cada escaneo lleva una huella SHA-256 ("registro" la vuelve a comprobar). Un rastreador toma el mercado de cada token escaneado cada ~30 minutos durante 7 días (DexScreener), así que máximos y mínimos son los observados en esos momentos; los movimientos entre muestras pueden perderse. Un evento cuenta en cuanto se ve; un "sin evento" necesita que pase toda la ventana con una instantánea en su último cuarto; si no, el caso queda sin resolver y no cuenta en ningún sentido. Precisión = verdaderos positivos / marcados; exhaustividad = verdaderos positivos / eventos; cobertura = resueltos / elegibles. Los rangos son intervalos de Wilson al 95%.',
     'Méthode : chaque scan éligible est le premier scan d’un token avec un verdict et un prix de marché, donc un token scanné plusieurs fois compte une fois. Les registres sont en ajout seul : une ligne de scan, son marché au moment du scan et chaque instantané ultérieur ne peuvent jamais être modifiés, et chaque scan porte une empreinte SHA-256 (« registre » la revérifie). Un traceur échantillonne le marché de chaque token scanné toutes les ~30 minutes pendant 7 jours (DexScreener) : pics et creux sont ceux observés à ces moments, des mouvements entre échantillons peuvent échapper. Un événement compte dès qu’il est vu ; un « pas d’événement » exige que toute la fenêtre passe avec un instantané dans son dernier quart, sinon le cas reste non résolu et ne compte dans aucun sens. Précision = vrais positifs / signalés ; rappel = vrais positifs / événements ; couverture = résolus / éligibles. Les intervalles sont des intervalles de Wilson à 95 %.',
     'Methode: Jeder berücksichtigte Scan ist der erste Scan eines Tokens mit Urteil und Marktpreis, ein mehrfach gescannter Token zählt also einmal. Datensätze sind nur anhängbar: eine Scan-Zeile, ihr Markt zum Scanzeitpunkt und jeder spätere Markt-Snapshot können nie geändert werden, und jeder Scan trägt einen SHA-256-Fingerabdruck („Datensatz“ prüft ihn erneut). Ein Tracker erfasst den Markt jedes gescannten Tokens etwa alle 30 Minuten über 7 Tage (DexScreener); Hochs und Tiefs sind die zu diesen Zeitpunkten beobachteten, Bewegungen dazwischen können fehlen. Ein Ereignis zählt, sobald es gesehen wird; „kein Ereignis“ erfordert, dass das ganze Fenster mit einem Snapshot im letzten Viertel vergeht, sonst bleibt der Fall offen und zählt in keine Richtung. Präzision = richtig positive / markierte; Trefferquote = richtig positive / Ereignisse; Abdeckung = aufgelöste / berücksichtigte. Bereiche sind 95-%-Wilson-Intervalle.',
     'Metode: setiap pindaian yang memenuhi syarat adalah pindaian pertama token dengan vonis dan harga pasar, jadi token yang dipindai berkali-kali dihitung sekali. Catatan hanya bisa ditambah: baris pindaian, pasar saat dipindai, dan setiap snapshot pasar setelahnya tidak pernah bisa diubah, dan setiap pindaian membawa sidik SHA-256 ("catatan" memeriksanya ulang). Pelacak mengambil sampel pasar setiap token yang dipindai tiap ~30 menit selama 7 hari (DexScreener), jadi puncak dan lembah adalah yang teramati saat itu; pergerakan di antara sampel bisa terlewat. Peristiwa dihitung begitu terlihat; "tidak ada peristiwa" butuh seluruh jendela berlalu dengan snapshot di seperempat terakhirnya, jika tidak kasus tetap belum terselesaikan dan tidak dihitung ke mana pun. Presisi = positif benar / ditandai; recall = positif benar / peristiwa; cakupan = terselesaikan / memenuhi syarat. Rentang adalah interval Wilson 95%.',
     'Paraan: ang bawat kwalipikadong scan ay ang unang scan ng token na may hatol at presyo sa merkado, kaya isang beses lang binibilang ang token na maraming beses na-scan. Pagdaragdag lang ang mga record: hindi kailanman mababago ang scan row, ang market nito sa oras ng scan at bawat sumunod na market snapshot, at may SHA-256 fingerprint ang bawat scan (sinusuri itong muli ng "record"). Kumukuha ang tracker ng sample ng market ng bawat na-scan na token tuwing ~30 minuto sa loob ng 7 araw (DexScreener), kaya ang mga pinakamataas at pinakamababa ay ang nakita sa mga oras na iyon; maaaring hindi makita ang galaw sa pagitan ng mga sample. Binibilang ang pangyayari sa sandaling makita; ang "walang pangyayari" ay nangangailangang lumipas ang buong window na may snapshot sa huling ikaapat nito, kung hindi ay mananatiling hindi nalulutas ang kaso at hindi binibilang. Precision = tunay na positibo / na-flag; recall = tunay na positibo / pangyayari; saklaw = nalutas / kwalipikado. Ang mga saklaw ay 95% Wilson interval.',
     'Phương pháp: mỗi lượt quét đủ điều kiện là lượt quét đầu tiên của token có kết luận và giá thị trường, nên token được quét nhiều lần chỉ tính một lần. Bản ghi chỉ được thêm: dòng quét, thị trường lúc quét và mọi ảnh chụp thị trường sau đó không bao giờ sửa được, và mỗi lượt quét có dấu vân tay SHA-256 ("bản ghi" kiểm tra lại). Bộ theo dõi lấy mẫu thị trường của mọi token đã quét khoảng 30 phút một lần trong 7 ngày (DexScreener), nên đỉnh và đáy là những gì quan sát được vào các thời điểm đó; biến động giữa các mẫu có thể bị bỏ sót. Sự kiện được tính ngay khi thấy; "không có sự kiện" cần cả khung trôi qua với một ảnh chụp trong phần tư cuối, nếu không trường hợp vẫn chưa xác định và không được tính. Độ chính xác = dương tính thật / bị gắn cờ; recall = dương tính thật / sự kiện; độ phủ = đã xác định / đủ điều kiện. Khoảng là khoảng Wilson 95%.'],
    ['No scans in this class for these settings. Change "show" or widen the period.', '在这些设置下没有该类别的扫描。请修改“显示”或扩大时间范围。', 'No hay escaneos de esta clase con estos ajustes. Cambia "mostrar" o amplía el periodo.', 'Aucun scan de cette classe avec ces réglages. Modifiez « afficher » ou élargissez la période.', 'Keine Scans dieser Klasse mit diesen Einstellungen. Ändere „anzeigen“ oder erweitere den Zeitraum.', 'Tidak ada pindaian kelas ini dengan pengaturan ini. Ubah "tampilkan" atau perlebar periode.', 'Walang scan sa klaseng ito sa mga setting na ito. Baguhin ang "ipakita" o palawakin ang panahon.', 'Không có lượt quét nào thuộc nhóm này với thiết lập hiện tại. Đổi "hiển thị" hoặc mở rộng khoảng thời gian.'],
    ['No eligible scans in this period yet. Scans need a verdict and a price at scan time; outcomes appear as the tracker samples the market.', '此期间尚无符合条件的扫描。扫描需在扫描时有结论和价格；结果会随追踪器采样市场而出现。', 'Aún no hay escaneos elegibles en este periodo. Los escaneos necesitan un veredicto y un precio al escanear; los resultados aparecen cuando el rastreador muestrea el mercado.', 'Aucun scan éligible sur cette période pour l’instant. Un scan doit avoir un verdict et un prix au moment du scan ; les résultats apparaissent à mesure que le traceur échantillonne le marché.', 'Noch keine berücksichtigten Scans in diesem Zeitraum. Scans brauchen ein Urteil und einen Preis zum Scanzeitpunkt; Ergebnisse erscheinen, sobald der Tracker den Markt erfasst.', 'Belum ada pindaian yang memenuhi syarat pada periode ini. Pindaian butuh vonis dan harga saat dipindai; hasil muncul saat pelacak mengambil sampel pasar.', 'Wala pang kwalipikadong scan sa panahong ito. Kailangan ng scan ng hatol at presyo sa oras ng scan; lumalabas ang resulta habang kumukuha ng sample ng market ang tracker.', 'Chưa có lượt quét đủ điều kiện trong khoảng này. Lượt quét cần kết luận và giá lúc quét; kết quả xuất hiện khi bộ theo dõi lấy mẫu thị trường.'],
    ['Could not load. Try again in a moment.', '加载失败，请稍后重试。', 'No se pudo cargar. Inténtalo de nuevo en un momento.', 'Chargement impossible. Réessayez dans un instant.', 'Laden fehlgeschlagen. Gleich noch einmal versuchen.', 'Gagal memuat. Coba lagi sebentar lagi.', 'Hindi ma-load. Subukang muli mamaya.', 'Không tải được. Hãy thử lại sau giây lát.']
  ];

  // dynamic risk explanations: [regex, [zh, es, fr, de, id, fil, vi]]; $n raw group, %n translated group
  var X = [
    [/^(\d+) (CLEAN|OK|RISKY|DANGER) · (.+)$/, ['$1 $2 · %3', '$1 $2 · %3', '$1 $2 · %3', '$1 $2 · %3', '$1 $2 · %3', '$1 $2 · %3', '$1 $2 · %3']],
    [/^soft: (.+)$/, ['%1', '%1', '%1', '%1', '%1', '%1', '%1']],
    [/^(\d+) wallets → (\d+) operators, biggest holds ([\d.]+)% of float \(([\d.]+)% of supply\)(.*)$/,
     ['$1 个钱包 → $2 个操盘方，最大一方持有 $3% 流通量（$4% 总量）%5',
      '$1 carteras → $2 operadores; el mayor tiene el $3% del float ($4% del suministro)%5',
      '$1 portefeuilles → $2 opérateurs ; le plus gros détient $3 % du flottant ($4 % de l’offre)%5',
      '$1 Wallets → $2 Operatoren, der größte hält $3 % des Floats ($4 % der Menge)%5',
      '$1 dompet → $2 operator, terbesar memegang $3% float ($4% suplai)%5',
      '$1 wallet → $2 operator, ang pinakamalaki ay may hawak na $3% ng float ($4% ng supply)%5',
      '$1 ví → $2 nhà điều hành, lớn nhất nắm $3% lưu hành ($4% tổng cung)%5']],
    [/^, could move price −(\d+)% if sold$/, ['，若卖出可使价格下跌 $1%', ', podría mover el precio −$1% si vende', ', pourrait faire baisser le prix de $1 % en vendant', ', könnte den Preis beim Verkauf um $1 % drücken', ', bisa menurunkan harga −$1% jika dijual', ', maaaring ibaba ang presyo nang −$1% kapag ibinenta', ', có thể làm giá giảm $1% nếu bán']],
    [/^, could move price <1% if sold$/, ['，若卖出价格变动 <1%', ', movería el precio <1% si vende', ', ferait bouger le prix de <1 % en vendant', ', würde den Preis beim Verkauf um <1 % bewegen', ', menggerakkan harga <1% jika dijual', ', gagalaw ang presyo nang <1% kapag ibinenta', ', làm giá thay đổi <1% nếu bán']],
    [/^, liquidity not measured$/, ['，流动性未测得', ', liquidez no medida', ', liquidité non mesurée', ', Liquidität nicht gemessen', ', likuiditas tidak terukur', ', hindi nasukat ang liquidity', ', chưa đo được thanh khoản']],
    [/^biggest operator could move price −(\d+)% if sold \(≥ (\d+)%; ([\d.]+)% of float\)$/,
     ['最大操盘方若卖出可使价格下跌 $1%（≥ $2%；占流通量 $3%）', 'el mayor operador podría mover el precio −$1% si vende (≥ $2%; $3% del float)', 'le plus gros opérateur pourrait faire baisser le prix de $1 % en vendant (≥ $2 % ; $3 % du flottant)', 'der größte Operator könnte den Preis beim Verkauf um $1 % drücken (≥ $2 %; $3 % des Floats)', 'operator terbesar bisa menurunkan harga −$1% jika menjual (≥ $2%; $3% float)', 'maaaring ibaba ng pinakamalaking operator ang presyo nang −$1% kapag nagbenta (≥ $2%; $3% ng float)', 'nhà điều hành lớn nhất có thể làm giá giảm $1% nếu bán (≥ $2%; $3% lưu hành)']],
    [/^biggest operator could move price −(\d+)% if sold \(([\d.]+)% of float\)$/,
     ['最大操盘方若卖出可使价格下跌 $1%（占流通量 $2%）', 'el mayor operador podría mover el precio −$1% si vende ($2% del float)', 'le plus gros opérateur pourrait faire baisser le prix de $1 % en vendant ($2 % du flottant)', 'der größte Operator könnte den Preis beim Verkauf um $1 % drücken ($2 % des Floats)', 'operator terbesar bisa menurunkan harga −$1% jika menjual ($2% float)', 'maaaring ibaba ng pinakamalaking operator ang presyo nang −$1% kapag nagbenta ($2% ng float)', 'nhà điều hành lớn nhất có thể làm giá giảm $1% nếu bán ($2% lưu hành)']],
    [/^biggest operator holds ([\d.]+)% of float \(liquidity not measured\)$/,
     ['最大操盘方持有流通量的 $1%（流动性未测得）', 'el mayor operador tiene el $1% del float (liquidez no medida)', 'le plus gros opérateur détient $1 % du flottant (liquidité non mesurée)', 'der größte Operator hält $1 % des Floats (Liquidität nicht gemessen)', 'operator terbesar memegang $1% float (likuiditas tidak terukur)', 'may hawak ang pinakamalaking operator na $1% ng float (hindi nasukat ang liquidity)', 'nhà điều hành lớn nhất nắm $1% lưu hành (chưa đo được thanh khoản)']],
    [/^(\d+)% virgin wallets in top \(≥ (\d+)%\)$/,
     ['头部钱包中 $1% 为新号（≥ $2%）', '$1% de carteras vírgenes en el top (≥ $2%)', '$1 % de portefeuilles vierges dans le top (≥ $2 %)', '$1 % jungfräuliche Wallets in den Top (≥ $2 %)', '$1% dompet perawan di jajaran teratas (≥ $2%)', '$1% bagong wallet sa itaas (≥ $2%)', '$1% ví trắng trong nhóm đầu (≥ $2%)']],
    [/^(\d+)% virgin wallets in top$/,
     ['头部钱包中 $1% 为新号', '$1% de carteras vírgenes en el top', '$1 % de portefeuilles vierges dans le top', '$1 % jungfräuliche Wallets in den Top', '$1% dompet perawan di jajaran teratas', '$1% bagong wallet sa itaas', '$1% ví trắng trong nhóm đầu']],
    [/^([\d.]+)% of float received by transfer \(≥ (\d+)%\)$/,
     ['流通量的 $1% 通过转账获得（≥ $2%）', 'el $1% del float se recibió por transferencia (≥ $2%)', '$1 % du flottant reçu par transfert (≥ $2 %)', '$1 % des Floats per Transfer erhalten (≥ $2 %)', '$1% float diterima lewat transfer (≥ $2%)', '$1% ng float ay natanggap sa transfer (≥ $2%)', '$1% lưu hành nhận qua chuyển khoản (≥ $2%)']],
    [/^([\d.]+)% of float received by transfer$/,
     ['流通量的 $1% 通过转账获得', 'el $1% del float se recibió por transferencia', '$1 % du flottant reçu par transfert', '$1 % des Floats per Transfer erhalten', '$1% float diterima lewat transfer', '$1% ng float ay natanggap sa transfer', '$1% lưu hành nhận qua chuyển khoản']],
    [/^deployer could move price −(\d+)% if sold \(≥ (\d+)%; ([\d.]+)% of supply with vesting\)$/,
     ['部署者若卖出可使价格下跌 $1%（≥ $2%；含锁仓占总量 $3%）', 'el desplegador podría mover el precio −$1% si vende (≥ $2%; $3% del suministro con vesting)', 'le déployeur pourrait faire baisser le prix de $1 % en vendant (≥ $2 % ; $3 % de l’offre avec vesting)', 'der Deployer könnte den Preis beim Verkauf um $1 % drücken (≥ $2 %; $3 % der Menge inkl. Vesting)', 'deployer bisa menurunkan harga −$1% jika menjual (≥ $2%; $3% suplai termasuk vesting)', 'maaaring ibaba ng deployer ang presyo nang −$1% kapag nagbenta (≥ $2%; $3% ng supply kasama ang vesting)', 'người triển khai có thể làm giá giảm $1% nếu bán (≥ $2%; $3% tổng cung gồm vesting)']],
    [/^one holder could move price −(\d+)% \(thin liquidity\)$/,
     ['单个持有者可使价格下跌 $1%（流动性薄）', 'un holder podría mover el precio −$1% (liquidez escasa)', 'un seul détenteur pourrait faire baisser le prix de $1 % (liquidité faible)', 'ein Holder könnte den Preis um $1 % drücken (dünne Liquidität)', 'satu holder bisa menurunkan harga −$1% (likuiditas tipis)', 'maaaring ibaba ng isang holder ang presyo nang −$1% (manipis na liquidity)', 'một holder có thể làm giá giảm $1% (thanh khoản mỏng)']],
    [/^biggest holder couldn't be fully read; could move price −(\d+)% \(thin liquidity\)$/,
     ['最大持有者未能完整读取；可使价格下跌 $1%（流动性薄）', 'no se pudo leer del todo al mayor holder; podría mover el precio −$1% (liquidez escasa)', 'le plus gros détenteur n’a pas pu être entièrement lu ; il pourrait faire baisser le prix de $1 % (liquidité faible)', 'der größte Holder konnte nicht vollständig gelesen werden; könnte den Preis um $1 % drücken (dünne Liquidität)', 'holder terbesar tidak terbaca penuh; bisa menurunkan harga −$1% (likuiditas tipis)', 'hindi nabasa nang buo ang pinakamalaking holder; maaaring ibaba ang presyo nang −$1% (manipis na liquidity)', 'không đọc hết được holder lớn nhất; có thể làm giá giảm $1% (thanh khoản mỏng)']],
    [/^pack of (\d+) wallets, ([\d.]+)% of float$/,
     ['$1 个钱包组成的团伙，占流通量 $2%', 'manada de $1 carteras, $2% del float', 'meute de $1 portefeuilles, $2 % du flottant', 'Rudel aus $1 Wallets, $2 % des Floats', 'kawanan $1 dompet, $2% float', 'pangkat ng $1 wallet, $2% ng float', 'bầy $1 ví, $2% lưu hành']],
    [/^proven operator of (\d+) wallets, ([\d.]+)% of float$/,
     ['已证实的 $1 钱包操盘方，占流通量 $2%', 'operador probado de $1 carteras, $2% del float', 'opérateur prouvé de $1 portefeuilles, $2 % du flottant', 'bewiesener Operator aus $1 Wallets, $2 % des Floats', 'operator terbukti dari $1 dompet, $2% float', 'napatunayang operator ng $1 wallet, $2% ng float', 'nhà điều hành đã chứng minh gồm $1 ví, $2% lưu hành']],
    [/^liquidity too thin \((\$[\d,]+)\)$/,
     ['流动性过薄（$1）', 'liquidez demasiado escasa ($1)', 'liquidité trop faible ($1)', 'Liquidität zu dünn ($1)', 'likuiditas terlalu tipis ($1)', 'masyadong manipis ang liquidity ($1)', 'thanh khoản quá mỏng ($1)']],
    [/^snipers hold ([\d.]+)% of float$/,
     ['狙击者持有流通量的 $1%', 'los snipers tienen el $1% del float', 'les snipers détiennent $1 % du flottant', 'Sniper halten $1 % des Floats', 'sniper memegang $1% float', 'may hawak ang mga sniper na $1% ng float', 'sniper nắm $1% lưu hành']],
    [/^top (\d+) hold ([\d.]+)% of float$/,
     ['前 $1 名持有流通量的 $2%', 'el top $1 tiene el $2% del float', 'le top $1 détient $2 % du flottant', 'die Top $1 halten $2 % des Floats', '$1 teratas memegang $2% float', 'may hawak ang top $1 na $2% ng float', 'top $1 nắm $2% lưu hành']],
    [/^no clear signs of a single operator$/, ['没有单一操盘方的明显迹象', 'sin señales claras de un único operador', 'aucun signe clair d’un opérateur unique', 'keine klaren Anzeichen für einen einzelnen Operator', 'tidak ada tanda jelas satu operator', 'walang malinaw na palatandaan ng iisang operator', 'không có dấu hiệu rõ của một nhà điều hành duy nhất']],
    [/^virgin share unknown: most of the top couldn't be read$/, ['新号占比未知：大部分头部钱包无法读取', 'proporción de vírgenes desconocida: no se pudo leer la mayor parte del top', 'part de vierges inconnue : la plupart du top n’a pas pu être lue', 'Anteil jungfräulicher Wallets unbekannt: der Großteil der Top konnte nicht gelesen werden', 'porsi perawan tidak diketahui: sebagian besar teratas tak terbaca', 'hindi alam ang bahagi ng bagong wallet: hindi nabasa ang karamihan sa itaas', 'chưa rõ tỷ lệ ví trắng: không đọc được phần lớn nhóm đầu']],
    [/^fewer than (\d+) holders \((\d+)\) — too early or too late$/, ['持有者少于 $1 个（$2）— 太早或太晚', 'menos de $1 holders ($2): demasiado pronto o demasiado tarde', 'moins de $1 détenteurs ($2) : trop tôt ou trop tard', 'weniger als $1 Holder ($2) — zu früh oder zu spät', 'kurang dari $1 holder ($2) — terlalu dini atau terlambat', 'kulang sa $1 holder ($2) — masyadong maaga o huli', 'ít hơn $1 holder ($2) — quá sớm hoặc quá muộn']],
    [/^probably rug −(\d+)%$/, ['可能跑路 −$1%', 'probablemente rug −$1%', 'probablement un rug −$1 %', 'wahrscheinlich Rug −$1 %', 'kemungkinan rug −$1%', 'malamang rug −$1%', 'có thể rug −$1%']],
    [/^probably rug −(\d+)% if suspicious holders sell$/, ['可疑持有者卖出则可能跑路 −$1%', 'probablemente rug −$1% si venden los holders sospechosos', 'probablement un rug −$1 % si les détenteurs suspects vendent', 'wahrscheinlich Rug −$1 %, wenn verdächtige Holder verkaufen', 'kemungkinan rug −$1% jika holder mencurigakan menjual', 'malamang rug −$1% kung magbenta ang mga kahina-hinalang holder', 'có thể rug −$1% nếu holder đáng ngờ bán']],
    [/^suspicious supply · ([\d.]+)% of float$/, ['可疑筹码 · 流通量 $1%', 'suministro sospechoso · $1% del float', 'offre suspecte · $1 % du flottant', 'verdächtige Menge · $1 % des Floats', 'suplai mencurigakan · $1% float', 'kahina-hinalang supply · $1% ng float', 'nguồn cung đáng ngờ · $1% lưu hành']],
    [/^A conditional scenario, not a prediction: if these wallets sell into the current liquidity(?: \(all priced pools together\))?, the price could fall about (\d+)%\.$/,
     ['这是条件情景而非预测：若这些钱包向当前流动性卖出，价格可能下跌约 $1%。', 'Un escenario condicional, no una predicción: si estas carteras venden en la liquidez actual, el precio podría caer cerca de un $1%.', 'Un scénario conditionnel, pas une prédiction : si ces portefeuilles vendent dans la liquidité actuelle, le prix pourrait chuter d’environ $1 %.', 'Ein bedingtes Szenario, keine Vorhersage: Wenn diese Wallets in die aktuelle Liquidität verkaufen, könnte der Preis um etwa $1 % fallen.', 'Skenario bersyarat, bukan prediksi: jika dompet-dompet ini menjual ke likuiditas saat ini, harga bisa turun sekitar $1%.', 'Isang kondisyonal na senaryo, hindi hula: kung ibenta ng mga wallet na ito sa kasalukuyang liquidity, maaaring bumagsak ang presyo nang mga $1%.', 'Đây là kịch bản có điều kiện, không phải dự đoán: nếu các ví này bán vào thanh khoản hiện tại, giá có thể giảm khoảng $1%.']],
    // radar reasons
    [/^verdict (\w+) \(was (\w+)\)$/, ['结论变为 $1（原为 $2）', 'veredicto $1 (antes $2)', 'verdict $1 (avant : $2)', 'Urteil $1 (vorher $2)', 'vonis $1 (sebelumnya $2)', 'hatol $1 (dati $2)', 'kết luận $1 (trước là $2)']],
    [/^verdict slipped from (\w+) to (\w+)$/, ['结论从 $1 降为 $2', 'el veredicto bajó de $1 a $2', 'le verdict est passé de $1 à $2', 'Urteil von $1 auf $2 gefallen', 'vonis turun dari $1 ke $2', 'bumaba ang hatol mula $1 tungong $2', 'kết luận giảm từ $1 xuống $2']],
    [/^probably-rug projection on the latest scan$/, ['最新扫描出现可能跑路预测', 'proyección de probable rug en el último escaneo', 'projection de rug probable sur le dernier scan', 'Rug-Projektion im letzten Scan', 'proyeksi kemungkinan rug pada pindaian terbaru', 'may projection ng malamang na rug sa pinakabagong scan', 'dự báo có thể rug ở lần quét mới nhất']],
    [/^largest operator's share fell ([\d.]+) points \(selling or moving\)$/, ['最大操盘方占比下降 $1 个百分点（卖出或转移）', 'la participación del mayor operador cayó $1 puntos (venta o traslado)', 'la part du plus gros opérateur a baissé de $1 points (vente ou transfert)', 'Anteil des größten Operators um $1 Punkte gesunken (Verkauf oder Umschichtung)', 'porsi operator terbesar turun $1 poin (menjual atau memindahkan)', 'bumaba nang $1 puntos ang bahagi ng pinakamalaking operator (nagbebenta o naglilipat)', 'tỷ lệ của nhà điều hành lớn nhất giảm $1 điểm (bán hoặc chuyển)']],
    [/^launch bundle sold another ([\d.]+)% of supply$/, ['上线捆绑又卖出总量的 $1%', 'el bundle de lanzamiento vendió otro $1% del suministro', 'le bundle de lancement a vendu encore $1 % de l’offre', 'das Launch-Bundle hat weitere $1 % der Menge verkauft', 'bundle peluncuran menjual $1% suplai lagi', 'nagbenta pa ang launch bundle ng $1% ng supply', 'gói ra mắt đã bán thêm $1% tổng cung']],
    [/^launch bundle still holds (\d+)% of supply$/, ['上线捆绑仍持有总量的 $1%', 'el bundle de lanzamiento aún tiene el $1% del suministro', 'le bundle de lancement détient encore $1 % de l’offre', 'das Launch-Bundle hält noch $1 % der Menge', 'bundle peluncuran masih memegang $1% suplai', 'may hawak pa ang launch bundle na $1% ng supply', 'gói ra mắt vẫn nắm $1% tổng cung']],
    [/^market moved against the scan: (.+)$/, ['市场走势不利于扫描结论：$1', 'el mercado se movió en contra del escaneo: $1', 'le marché a évolué contre le scan : $1', 'der Markt hat sich gegen den Scan bewegt: $1', 'pasar bergerak berlawanan dengan pindaian: $1', 'gumalaw ang merkado laban sa scan: $1', 'thị trường đi ngược kết quả quét: $1']],
    [/^collapsed since the scan(.*)$/, ['扫描后已崩盘$1', 'colapsó desde el escaneo$1', 's’est effondré depuis le scan$1', 'seit dem Scan eingebrochen$1', 'ambruk sejak pindaian$1', 'bumagsak mula sa scan$1', 'đã sụp đổ từ lúc quét$1']],
    [/^outcome: rugged$/, ['结果：已跑路', 'resultado: rug', 'résultat : rug', 'Ergebnis: Rug', 'hasil: rug', 'kinalabasan: rug', 'kết quả: đã rug']],
    [/^latest scan is ([\d.]+) h old$/, ['最新扫描已过去 $1 小时', 'el último escaneo tiene $1 h', 'le dernier scan date de $1 h', 'letzter Scan ist $1 h alt', 'pindaian terbaru berusia $1 jam', '$1 oras na ang pinakabagong scan', 'lần quét mới nhất đã $1 giờ']],
    [/^scan is (\d+) min old \(stale\)$/, ['扫描已过去 $1 分钟（过期）', 'escaneo de hace $1 min (desactualizado)', 'scan vieux de $1 min (périmé)', 'Scan ist $1 Min. alt (veraltet)', 'pindaian berusia $1 menit (usang)', '$1 minuto na ang scan (luma)', 'đã quét $1 phút trước (cũ)']],
    // relative times and status lines
    [/^(\d+)m ago$/, ['$1 分钟前', 'hace $1 min', 'il y a $1 min', 'vor $1 Min.', '$1 menit lalu', '$1 minuto ang nakalipas', '$1 phút trước']],
    [/^(\d+)h ago$/, ['$1 小时前', 'hace $1 h', 'il y a $1 h', 'vor $1 Std.', '$1 jam lalu', '$1 oras ang nakalipas', '$1 giờ trước']],
    [/^(\d+)d ago$/, ['$1 天前', 'hace $1 d', 'il y a $1 j', 'vor $1 T.', '$1 hari lalu', '$1 araw ang nakalipas', '$1 ngày trước']],
    [/^updated (.+) · every (\d+) s$/, ['已更新 $1 · 每 $2 秒', 'actualizado $1 · cada $2 s', 'mis à jour $1 · toutes les $2 s', 'aktualisiert $1 · alle $2 s', 'diperbarui $1 · tiap $2 dtk', 'na-update $1 · bawat $2 s', 'cập nhật $1 · mỗi $2 giây']],
    [/^Unavailable: (.+)$/, ['不可用：%1', 'No disponible: %1', 'Indisponible : %1', 'Nicht verfügbar: %1', 'Tidak tersedia: %1', 'Hindi available: %1', 'Không khả dụng: %1']],
    [/^memory is off$/, ['记忆库已关闭', 'la memoria está desactivada', 'la mémoire est désactivée', 'Gedächtnis ist aus', 'memori nonaktif', 'naka-off ang memorya', 'bộ nhớ đang tắt']],
    // counts and shares in the result
    [/^(\d+) holders$/, ['$1 个持有者', '$1 holders', '$1 détenteurs', '$1 Holder', '$1 holder', '$1 holder', '$1 holder']],
    [/^(\d+) wallets?( ·)?$/, ['$1 个钱包$2', '$1 carteras$2', '$1 portefeuilles$2', '$1 Wallets$2', '$1 dompet$2', '$1 wallet$2', '$1 ví$2']],
    [/^(\d+) pools?$/, ['$1 个池子', '$1 pools', '$1 pools', '$1 Pools', '$1 pool', '$1 pool', '$1 pool']],
    [/^(\d+) candles$/, ['$1 根 K 线', '$1 velas', '$1 bougies', '$1 Kerzen', '$1 candle', '$1 candle', '$1 nến']],
    [/^(\d+) events$/, ['$1 个事件', '$1 eventos', '$1 événements', '$1 Ereignisse', '$1 peristiwa', '$1 event', '$1 sự kiện']],
    [/^([<\d.]+)% of float$/, ['流通量的 $1%', '$1% del float', '$1 % du flottant', '$1 % des Floats', '$1% float', '$1% ng float', '$1% lưu hành']],
    [/^([<\d.]+)% of supply$/, ['总量的 $1%', '$1% del suministro', '$1 % de l’offre', '$1 % der Menge', '$1% suplai', '$1% ng supply', '$1% tổng cung']],
    [/^(\d+) criteria · (\d+) flagged$/, ['$1 项检查 · $2 项标记', '$1 criterios · $2 señalados', '$1 critères · $2 signalés', '$1 Kriterien · $2 markiert', '$1 kriteria · $2 ditandai', '$1 pamantayan · $2 na-flag', '$1 tiêu chí · $2 bị gắn cờ']],
    [/^(\d+) of (\d+) holders · ([\d.]+)% of float$/, ['$2 个持有者中 $1 个 · 流通量 $3%', '$1 de $2 holders · $3% del float', '$1 détenteurs sur $2 · $3 % du flottant', '$1 von $2 Holdern · $3 % des Floats', '$1 dari $2 holder · $3% float', '$1 sa $2 holder · $3% ng float', '$1/$2 holder · $3% lưu hành']],
    [/^(\d+) wallets → (\d+) operators · dump −(\d+)%$/, ['$1 个钱包 → $2 个操盘方 · 抛售 −$3%', '$1 carteras → $2 operadores · venta −$3%', '$1 portefeuilles → $2 opérateurs · vente −$3 %', '$1 Wallets → $2 Operatoren · Verkauf −$3 %', '$1 dompet → $2 operator · jual −$3%', '$1 wallet → $2 operator · bagsak −$3%', '$1 ví → $2 nhà điều hành · xả −$3%']],
    [/^top 10 hold ([\d.]+)% · #1 holds ([\d.]+)%$/, ['前 10 持有 $1% · 第 1 名持有 $2%', 'el top 10 tiene el $1% · el #1 tiene el $2%', 'le top 10 détient $1 % · le n° 1 détient $2 %', 'Top 10 halten $1 % · Nr. 1 hält $2 %', '10 teratas memegang $1% · #1 memegang $2%', 'hawak ng top 10 ang $1% · hawak ng #1 ang $2%', 'top 10 nắm $1% · #1 nắm $2%']],
    [/^([\d.]+)s · (\d+) RPC requests$/, ['$1 秒 · $2 次 RPC 请求', '$1 s · $2 solicitudes RPC', '$1 s · $2 requêtes RPC', '$1 s · $2 RPC-Anfragen', '$1 dtk · $2 permintaan RPC', '$1s · $2 RPC request', '$1 giây · $2 yêu cầu RPC']],
    [/^(\d+) launch wallets? · block (.+)$/, ['$1 个上线钱包 · 区块 $2', '$1 carteras de lanzamiento · bloque $2', '$1 portefeuilles de lancement · bloc $2', '$1 Launch-Wallets · Block $2', '$1 dompet peluncuran · blok $2', '$1 launch wallet · block $2', '$1 ví ra mắt · khối $2']],
    [/^(\d+) links · (\d+) operators merged(.*)$/, ['$1 条关联 · 合并 $2 个操盘方%3', '$1 vínculos · $2 operadores agrupados%3', '$1 liens · $2 opérateurs regroupés%3', '$1 Verbindungen · $2 Operatoren zusammengefasst%3', '$1 tautan · $2 operator digabung%3', '$1 ugnay · $2 operator ang pinagsama%3', '$1 liên kết · gộp $2 nhà điều hành%3']],
    [/^ · (\d+) hub funders? \(not linking\)$/, [' · $1 个枢纽资金方（不关联）', ' · $1 financiadores hub (sin vincular)', ' · $1 financeurs hub (sans lien)', ' · $1 Hub-Finanzierer (ohne Verbindung)', ' · $1 pendana hub (tidak menghubungkan)', ' · $1 hub na nagpopondo (hindi nag-uugnay)', ' · $1 nguồn vốn trung tâm (không nối)']],
    [/^funded (\d+) of the top (\d+) · ([\d.]+)% of float · (\d+\+?) outgoing transfers: exchange-like, not a link$/,
     ['为前 $2 名中的 $1 个注资 · 流通量 $3% · $4 笔转出：类似交易所，不算关联', 'financió a $1 del top $2 · $3% del float · $4 transferencias salientes: tipo exchange, no es vínculo', 'a financé $1 du top $2 · $3 % du flottant · $4 transferts sortants : type exchange, pas un lien', 'hat $1 der Top $2 finanziert · $3 % des Floats · $4 ausgehende Transfers: börsenähnlich, keine Verbindung', 'mendanai $1 dari $2 teratas · $3% float · $4 transfer keluar: mirip bursa, bukan tautan', 'nagpondo sa $1 ng top $2 · $3% ng float · $4 palabas na transfer: parang exchange, hindi ugnay', 'cấp vốn cho $1/$2 ví đầu · $3% lưu hành · $4 lượt chuyển đi: kiểu sàn, không phải liên kết']],
    [/^funder (\S+) · funded (\d+) holders? · (\d+\+?) outgoing · hub: never links$/,
     ['资金方 $1 · 注资 $2 个持有者 · $3 笔转出 · 枢纽：不关联', 'financiador $1 · financió a $2 holders · $3 salientes · hub: nunca vincula', 'financeur $1 · a financé $2 détenteurs · $3 sortants · hub : ne relie jamais', 'Finanzierer $1 · hat $2 Holder finanziert · $3 ausgehend · Hub: verbindet nie', 'pendana $1 · mendanai $2 holder · $3 keluar · hub: tidak menghubungkan', 'nagpopondo $1 · nagpondo sa $2 holder · $3 palabas · hub: hindi nag-uugnay', 'nguồn vốn $1 · cấp cho $2 holder · $3 lượt chuyển đi · trung tâm: không nối']],
    [/^#(\d+) (\S+) · ([\d.]+)% of float$/, ['#$1 $2 · 流通量 $3%', '#$1 $2 · $3% del float', '#$1 $2 · $3 % du flottant', '#$1 $2 · $3 % des Floats', '#$1 $2 · $3% float', '#$1 $2 · $3% ng float', '#$1 $2 · $3% lưu hành']],
    [/^price · (\w+)$/, ['价格 · $1', 'precio · $1', 'prix · $1', 'Preis · $1', 'harga · $1', 'presyo · $1', 'giá · $1']],
    [/^model (\S+)$/, ['模型 $1', 'modelo $1', 'modèle $1', 'Modell $1', 'model $1', 'modelo $1', 'mô hình $1']],
    [/^(\S+) \(shadow\)$/, ['$1（影子）', '$1 (sombra)', '$1 (fantôme)', '$1 (Schatten)', '$1 (bayangan)', '$1 (shadow)', '$1 (bóng)']],
    [/^agrees: (\w+) (\d+)$/, ['一致：$1 $2', 'coincide: $1 $2', 'concorde : $1 $2', 'stimmt überein: $1 $2', 'sepakat: $1 $2', 'sang-ayon: $1 $2', 'đồng ý: $1 $2']],
    [/^price chart, probably rug −(\d+)%$/, ['价格图，可能跑路 −$1%', 'gráfico de precio, probablemente rug −$1%', 'graphique du prix, probablement un rug −$1 %', 'Preisdiagramm, wahrscheinlich Rug −$1 %', 'grafik harga, kemungkinan rug −$1%', 'chart ng presyo, malamang rug −$1%', 'biểu đồ giá, có thể rug −$1%']],
    [/^(\d+) holders · lower bound \(largest accounts only\)$/, ['$1 个持有者 · 下限（仅最大账户）', '$1 holders · mínimo (solo las cuentas mayores)', '$1 détenteurs · minimum (plus gros comptes seulement)', '$1 Holder · Untergrenze (nur größte Konten)', '$1 holder · batas bawah (hanya akun terbesar)', '$1 holder · minimum (pinakamalalaking account lang)', '$1 holder · tối thiểu (chỉ tài khoản lớn nhất)']],
    // radar and proof pages
    [/^Radar unavailable: (.+)$/, ['雷达不可用：%1', 'Radar no disponible: %1', 'Radar indisponible : %1', 'Radar nicht verfügbar: %1', 'Radar tidak tersedia: %1', 'Hindi available ang radar: %1', 'Radar không khả dụng: %1']],
    [/^(\d+(?:\.\d+)?) days?$/, ['$1 天', '$1 días', '$1 jours', '$1 Tage', '$1 hari', '$1 araw', '$1 ngày']],
    [/^(\d+) h$/, ['$1 小时', '$1 h', '$1 h', '$1 h', '$1 jam', '$1 oras', '$1 giờ']],
    [/^Every eligible scan \(each token's first scan with a verdict and a price\) and what its market did in the next (.+): market cap at scan, later market cap, observed peak and trough, and when it was evaluated\. Nothing is left out: successes and misses are counted together\.$/,
     ['每个符合条件的扫描（每个代币首次带结论和价格的扫描）及其市场在随后 %1 内的表现：扫描时市值、之后市值、观察到的峰值与谷值，以及评估时间。没有任何遗漏：成功与失误一并计算。',
      'Cada escaneo elegible (el primer escaneo de cada token con veredicto y precio) y lo que hizo su mercado en los siguientes %1: cap. al escanear, cap. posterior, máximo y mínimo observados y cuándo se evaluó. No se omite nada: aciertos y fallos se cuentan juntos.',
      'Chaque scan éligible (le premier scan de chaque token avec verdict et prix) et ce que son marché a fait dans les %1 suivants : capitalisation au scan, capitalisation ultérieure, pic et creux observés, et date d’évaluation. Rien n’est omis : réussites et échecs sont comptés ensemble.',
      'Jeder berücksichtigte Scan (der erste Scan jedes Tokens mit Urteil und Preis) und was sein Markt in den nächsten %1 tat: Marktkap. beim Scan, spätere Marktkap., beobachtetes Hoch und Tief und wann bewertet wurde. Nichts wird weggelassen: Treffer und Fehler zählen zusammen.',
      'Setiap pindaian yang memenuhi syarat (pindaian pertama tiap token dengan vonis dan harga) dan apa yang dilakukan pasarnya dalam %1 berikutnya: kap. saat dipindai, kap. setelahnya, puncak dan lembah teramati, dan kapan dievaluasi. Tidak ada yang dihilangkan: keberhasilan dan kegagalan dihitung bersama.',
      'Bawat kwalipikadong scan (ang unang scan ng bawat token na may hatol at presyo) at ang ginawa ng market nito sa susunod na %1: market cap sa scan, market cap pagkatapos, nakitang pinakamataas at pinakamababa, at kung kailan ito sinuri. Walang iniiwan: magkasamang binibilang ang tagumpay at sablay.',
      'Mọi lượt quét đủ điều kiện (lượt quét đầu tiên của mỗi token có kết luận và giá) và diễn biến thị trường của nó trong %1 tiếp theo: vốn hóa lúc quét, vốn hóa sau đó, đỉnh và đáy quan sát được, và thời điểm đánh giá. Không bỏ sót gì: trúng và trượt đều được tính.']],
    [/^Tokens rated DANGER at scan time whose market cap then fell at least (\d+)% within (.+)\. The full count of DANGER and non-DANGER scans, and the misses, are in the box below\.$/,
     ['扫描时评为 DANGER、随后在 %2 内市值下跌至少 $1% 的代币。DANGER 与非 DANGER 扫描的完整计数及失误见下方框。', 'Tokens calificados DANGER al escanear cuya cap. de mercado cayó luego al menos un $1% en %2. El recuento completo de escaneos DANGER y no DANGER, y los fallos, está en el recuadro de abajo.', 'Tokens notés DANGER au scan dont la capitalisation a ensuite chuté d’au moins $1 % en %2. Le décompte complet des scans DANGER et non DANGER, et les échecs, sont dans l’encadré ci-dessous.', 'Tokens mit Urteil DANGER beim Scan, deren Marktkap. danach innerhalb von %2 um mindestens $1 % fiel. Die vollständige Zahl der DANGER- und Nicht-DANGER-Scans und die Fehler stehen im Kasten unten.', 'Token dengan vonis DANGER saat dipindai yang kap. pasarnya lalu turun setidaknya $1% dalam %2. Jumlah lengkap pindaian DANGER dan non-DANGER, serta yang meleset, ada di kotak di bawah.', 'Mga token na DANGER sa oras ng scan na bumagsak ang market cap nang hindi bababa sa $1% sa loob ng %2. Nasa kahon sa ibaba ang buong bilang ng DANGER at hindi-DANGER na scan, at ang mga sablay.', 'Token bị đánh giá DANGER lúc quét mà vốn hóa sau đó giảm ít nhất $1% trong %2. Tổng số lượt quét DANGER và không DANGER, cùng các lần trượt, nằm trong ô bên dưới.']],
    [/^Tokens rated CLEAN at scan time whose market cap then rose at least ([\d.]+)× within (.+)\. The full count of CLEAN and non-CLEAN scans, and the misses, are in the box below\.$/,
     ['扫描时评为 CLEAN、随后在 %2 内市值上涨至少 $1× 的代币。CLEAN 与非 CLEAN 扫描的完整计数及失误见下方框。', 'Tokens calificados CLEAN al escanear cuya cap. de mercado subió luego al menos $1× en %2. El recuento completo de escaneos CLEAN y no CLEAN, y los fallos, está en el recuadro de abajo.', 'Tokens notés CLEAN au scan dont la capitalisation a ensuite monté d’au moins $1× en %2. Le décompte complet des scans CLEAN et non CLEAN, et les échecs, sont dans l’encadré ci-dessous.', 'Tokens mit Urteil CLEAN beim Scan, deren Marktkap. danach innerhalb von %2 um mindestens $1× stieg. Die vollständige Zahl der CLEAN- und Nicht-CLEAN-Scans und die Fehler stehen im Kasten unten.', 'Token dengan vonis CLEAN saat dipindai yang kap. pasarnya lalu naik setidaknya $1× dalam %2. Jumlah lengkap pindaian CLEAN dan non-CLEAN, serta yang meleset, ada di kotak di bawah.', 'Mga token na CLEAN sa oras ng scan na tumaas ang market cap nang hindi bababa sa $1× sa loob ng %2. Nasa kahon sa ibaba ang buong bilang ng CLEAN at hindi-CLEAN na scan, at ang mga sablay.', 'Token được đánh giá CLEAN lúc quét mà vốn hóa sau đó tăng ít nhất $1× trong %2. Tổng số lượt quét CLEAN và không CLEAN, cùng các lần trượt, nằm trong ô bên dưới.']],
    [/^flagged = verdict (DANGER|CLEAN) \(([\w-]+)\); event = market cap (fell ≥ (\d+)%|rose ≥ ([\d.]+)×) (at any point|by the window end) within (.+)$/,
     ['已标记 = 结论 $1（$2）；事件 = 市值%3%6，窗口 %7', 'marcado = veredicto $1 ($2); evento = cap. de mercado %3 %6 en %7', 'signalé = verdict $1 ($2) ; événement = capitalisation %3 %6 en %7', 'markiert = Urteil $1 ($2); Ereignis = Marktkap. %3 %6 innerhalb von %7', 'ditandai = vonis $1 ($2); peristiwa = kap. pasar %3 %6 dalam %7', 'na-flag = hatol $1 ($2); pangyayari = market cap %3 %6 sa loob ng %7', 'gắn cờ = kết luận $1 ($2); sự kiện = vốn hóa %3 %6 trong %7']],
    [/^fell ≥ (\d+)%$/, ['下跌 ≥ $1%', 'cayó ≥ $1%', 'a chuté ≥ $1 %', 'gefallen ≥ $1 %', 'turun ≥ $1%', 'bumagsak ≥ $1%', 'giảm ≥ $1%']],
    [/^rose ≥ ([\d.]+)×$/, ['上涨 ≥ $1×', 'subió ≥ $1×', 'a monté ≥ $1×', 'gestiegen ≥ $1×', 'naik ≥ $1×', 'tumaas ≥ $1×', 'tăng ≥ $1×']],
    [/^at any point$/, ['（任意时刻）', 'en cualquier momento', 'à tout moment', 'zu jedem Zeitpunkt', 'kapan pun', 'kahit kailan', 'tại bất kỳ thời điểm nào']],
    [/^by the window end$/, ['（窗口结束时）', 'al final de la ventana', 'à la fin de la fenêtre', 'bis zum Fensterende', 'di akhir jendela', 'sa dulo ng window', 'vào cuối khung']],
    [/^(\d+) snapshots?( · window open)?$/, ['$1 个快照%2', '$1 instantáneas%2', '$1 instantanés%2', '$1 Snapshots%2', '$1 snapshot%2', '$1 snapshot%2', '$1 ảnh chụp%2']],
    [/^ · window open$/, [' · 窗口未结束', ' · ventana abierta', ' · fenêtre ouverte', ' · Fenster offen', ' · jendela terbuka', ' · bukas ang window', ' · khung chưa đóng']],
    [/^Showing the newest (\d+) of (\d+)\.$/, ['显示最新的 $1 条，共 $2 条。', 'Se muestran los $1 más recientes de $2.', 'Affichage des $1 plus récents sur $2.', 'Die neuesten $1 von $2 werden angezeigt.', 'Menampilkan $1 terbaru dari $2.', 'Ipinapakita ang pinakabagong $1 sa $2.', 'Đang hiển thị $1 mới nhất trên $2.']],
    [/^([\d.]+)% \(([\d.]+)% coverage\)$/, ['$1%（覆盖 $2%）', '$1% ($2% de cobertura)', '$1 % ($2 % de couverture)', '$1 % ($2 % Abdeckung)', '$1% (cakupan $2%)', '$1% ($2% saklaw)', '$1% (độ phủ $2%)']],
    [/^(\d+) \(([\d.]+)% coverage\)$/, ['$1（覆盖 $2%）', '$1 ($2% de cobertura)', '$1 ($2 % de couverture)', '$1 ($2 % Abdeckung)', '$1 (cakupan $2%)', '$1 ($2% saklaw)', '$1 (độ phủ $2%)']],
    [/^(\d+) ((?:true|false) (?:positive|negative))$/, ['$1 %2', '$1 %2', '$1 %2', '$1 %2', '$1 %2', '$1 %2', '$1 %2']],
    // coverage notes
    [/^holder history read in windows: top holders approximate$/, ['持有者历史分段读取：头部持有者为近似值', 'historial de holders leído por ventanas: top aproximado', 'historique lu par fenêtres : top approximatif', 'Holder-Historie in Fenstern gelesen: Top-Holder näherungsweise', 'riwayat holder dibaca per jendela: holder teratas perkiraan', 'binasa nang paputol-putol ang kasaysayan: tantya lang ang top', 'lịch sử holder đọc theo từng đoạn: nhóm đầu là ước tính']],
    [/^(\d+) of (\d+) examined wallets unread$/, ['已检查的 $2 个钱包中有 $1 个未读取', '$1 de $2 carteras examinadas sin leer', '$1 portefeuilles examinés sur $2 non lus', '$1 von $2 geprüften Wallets nicht gelesen', '$1 dari $2 dompet yang diperiksa tak terbaca', '$1 sa $2 na sinuring wallet ang hindi nabasa', '$1 trên $2 ví đã kiểm tra chưa đọc được']],
    [/^holder count is a lower bound \(largest accounts only\)$/, ['持有者数量为下限（仅最大账户）', 'el número de holders es un mínimo (solo las cuentas mayores)', 'le nombre de détenteurs est un minimum (plus gros comptes seulement)', 'Holder-Anzahl ist eine Untergrenze (nur größte Konten)', 'jumlah holder adalah batas bawah (hanya akun terbesar)', 'minimum lang ang bilang ng holder (pinakamalalaking account lang)', 'số holder là mức tối thiểu (chỉ tài khoản lớn nhất)']]
  ];

  var IDX = {}, REV = {};   // REV: translated text -> English, for nodes a template engine cloned after translating
  for (var i = 0; i < P.length; i++) {
    IDX[P[i][0]] = P[i];
    for (var c = 1; c < P[i].length; c++) if (P[i][c] !== P[i][0] && !REV[P[i][c]]) REV[P[i][c]] = P[i][0];
  }
  var lang = 'en', col = 0;

  function fill(tpl, m) {
    return tpl.replace(/([$%])(\d)/g, function (_, k, n) { var g = m[+n] || ''; return k === '$' ? g : tr(g); });
  }
  function tr(s) {
    if (!col || !s) return s;
    var row = IDX[s];
    if (row) return row[col] || s;
    for (var j = 0; j < X.length; j++) {
      var m = X[j][0].exec(s);
      if (m) return fill(X[j][1][col - 1] || s, m);
    }
    return s;
  }
  // text nodes: keep surrounding whitespace, translate the trimmed core
  var ORIG = new WeakMap();
  function doText(n) {
    var v = n.nodeValue;
    if (!v || (!/[A-Za-z]/.test(v) && !REV[v.trim()])) return;   // English, or a clone of a translation
    var known = ORIG.get(n);
    var src = known && known.out === v ? known.src : v;   // the page may have written a new English value
    var core = src.trim();
    if (!core) return;
    if (!known && REV[core]) { src = src.replace(core, REV[core]); core = REV[core]; }   // a clone of a translated node
    var out = col ? src.replace(core, tr(core)) : src;
    if (out !== v) n.nodeValue = out;
    ORIG.set(n, {src: src, out: out});
  }
  // a heading split into one element per word (the hero): translated as one sentence; chain names keep their colour
  var CHAIN_WORDS = /(Robinhood|Solana|Base|BNB)/;
  function doWhole(el) {
    var w = el.__i18n;
    if (!w) {
      // a clone of a translated heading carries our sentence and hidden words: drop the one, show the others
      [].slice.call(el.querySelectorAll('[data-i18n-own]')).forEach(function (o) { o.parentNode.removeChild(o); });
      [].slice.call(el.children).forEach(function (k) { k.style.display = ''; });
      var kids = [].slice.call(el.children);   // one element per word, spaced by CSS: join the words with spaces
      w = el.__i18n = {src: (kids.length ? kids.map(function (k) { return k.textContent.trim(); }).join(' ') : el.textContent)
                         .replace(/\s+/g, ' ').trim(), kids: kids, color: {}};
      w.kids.forEach(function (k) { var c = k.style && k.style.color; if (c) w.color[k.textContent.replace(/[,.]/g, '')] = c; });
    }
    var out = col ? tr(w.src) : w.src, own = el.querySelector('[data-i18n-own]');
    if (own) own.parentNode.removeChild(own);
    if (out === w.src) { w.kids.forEach(function (k) { k.style.display = ''; }); return; }
    own = document.createElement('span');
    own.setAttribute('data-i18n-own', '1');
    own.setAttribute('data-no-i18n', '1');
    out.split(CHAIN_WORDS).forEach(function (part) {
      if (!part) return;
      if (w.color[part]) { var c = document.createElement('span'); c.style.color = w.color[part]; c.textContent = part; own.appendChild(c); }
      else own.appendChild(document.createTextNode(part));
    });
    w.kids.forEach(function (k) { k.style.display = 'none'; });
    el.appendChild(own);
  }
  var ATTRS = ['placeholder', 'title', 'aria-label'];
  function doEl(el) {
    for (var a = 0; a < ATTRS.length; a++) {
      var k = ATTRS[a], v = el.getAttribute && el.getAttribute(k);
      if (!v) continue;
      var key = 'data-i18n-' + k, src = el.getAttribute(key) || v;
      if (el.getAttribute(key) && tr(el.getAttribute(key)) !== v && el.getAttribute(key) !== v) src = v;   // changed by the page
      var out = col ? tr(src) : src;
      if (out !== src) el.setAttribute(key, src);
      if (out !== v) el.setAttribute(k, out);
    }
  }
  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) return doText(root);
    if (root.nodeType !== 1 || /^(SCRIPT|STYLE|TEXTAREA|CODE|PRE)$/.test(root.tagName) || root.hasAttribute('data-no-i18n')) return;
    if (root.hasAttribute('data-i18n-whole')) return doWhole(root);
    if (root.closest && root.closest('[data-i18n-whole]')) return;
    doEl(root);
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (n) {
        if (n.nodeType === 1 && (/^(SCRIPT|STYLE|TEXTAREA|CODE|PRE)$/.test(n.tagName) || n.hasAttribute('data-no-i18n')))
          return NodeFilter.FILTER_REJECT;
        if (n.nodeType === 1 && n.hasAttribute('data-i18n-whole')) { doWhole(n); return NodeFilter.FILTER_REJECT; }
        return NodeFilter.FILTER_ACCEPT;
      }});
    var n;
    while ((n = w.nextNode())) { if (n.nodeType === 3) doText(n); else doEl(n); }
  }

  function pick() {
    var q = (new URLSearchParams(location.search).get('lang') || '').toLowerCase();
    if (LANGS.indexOf(q) >= 0) return q;
    try { var s = localStorage.getItem(KEY); if (LANGS.indexOf(s) >= 0) return s; } catch (e) {}
    var nav = (navigator.language || 'en').toLowerCase();
    if (nav.indexOf('tl') === 0) return 'fil';
    for (var i = 0; i < LANGS.length; i++) if (nav.indexOf(LANGS[i]) === 0) return LANGS[i];
    return 'en';
  }
  function set(l) {
    lang = LANGS.indexOf(l) >= 0 ? l : 'en';
    col = LANGS.indexOf(lang);
    document.documentElement.lang = lang === 'fil' ? 'fil' : lang;
    try { localStorage.setItem(KEY, lang); } catch (e) {}
    walk(document.body);
    var sels = document.querySelectorAll('select[data-lang-select]');
    for (var i = 0; i < sels.length; i++) sels[i].value = lang;
  }
  // a template engine may clone the header: a cloned picker keeps the markup but loses its listener, so a picker
  // counts only if this script made it (expando __cm, never copied by cloneNode)
  function picker() {
    var slots = document.querySelectorAll('[data-lang-slot]');
    for (var i = 0; i < slots.length; i++) {
      var old = slots[i].querySelector('select[data-lang-select]');
      if (old && old.__cm) { if (old.value !== lang) old.value = lang; continue; }
      if (old) old.parentNode.removeChild(old);
      var s = document.createElement('select');
      s.__cm = true;
      s.setAttribute('data-lang-select', '1');
      s.setAttribute('data-no-i18n', '1');
      s.setAttribute('aria-label', 'language');
      s.style.cssText = "background:#0b1013;color:#9fd9ff;border:1px solid #1c252b;border-radius:8px;padding:4px 6px;font-family:'JetBrains Mono',monospace;font-size:12px;cursor:pointer";
      for (var j = 0; j < LANGS.length; j++) {
        var o = document.createElement('option'); o.value = LANGS[j]; o.textContent = NAME[LANGS[j]]; s.appendChild(o);
      }
      s.value = lang;
      s.addEventListener('change', function (e) { set(e.target.value); });
      slots[i].appendChild(s);
    }
  }

  // read now: the script loads in <head>, before a page rewrites its URL (the scanner drops ?lang= on its first render)
  var INIT = pick();
  var busy = false;
  var mo = new MutationObserver(function (list) {
    if (busy) return;
    busy = true;
    try {
      picker();
      var html = document.documentElement;
      if (html && html.lang !== lang) html.lang = lang;
      for (var i = 0; i < list.length; i++) {
        var r = list[i];
        if (r.target.closest ? r.target.closest('[data-i18n-whole],[data-no-i18n]') : r.target.parentNode && r.target.parentNode.closest &&
            r.target.parentNode.closest('[data-i18n-whole],[data-no-i18n]')) continue;
        if (r.type === 'characterData') doText(r.target);
        else if (r.type === 'attributes') doEl(r.target);
        else for (var j = 0; j < r.addedNodes.length; j++) walk(r.addedNodes[j]);
      }
    } finally { busy = false; }
  });

  function start() {
    picker();
    set(INIT);
    // the whole document, not the body: a bundled page replaces <html>'s content after load
    mo.observe(document, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS});
  }
  window.CrawlMemeI18n = {t: function (s) { return tr(s); }, set: set, langs: LANGS, names: NAME,
                          get lang() { return lang; }};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
