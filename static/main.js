function applyFilter(){
  const q = document.getElementById('quickSearch');
  if(!q) return;
  const val = q.value.toLowerCase();
  const rows = document.querySelectorAll('#cardsTable tbody tr.card-row');
  rows.forEach(r=>{
    r.style.display = r.innerText.toLowerCase().includes(val) ? '' : 'none';
  });
}
function confirmDelete(formId){
  if(confirm('Delete this card?')) document.getElementById(formId).submit();
}
