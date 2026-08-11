'use strict';

/**
 * El rubro llega como texto libre del formulario. Aca se lo lleva a una clave
 * de plantilla, tolerando acentos, mayusculas, plurales y sinonimos.
 *
 * Regla al agregar sinonimos: que sean especificos del rubro. Palabras como
 * "venta" o "local" aplican a cualquier negocio y hacen que "venta de autos"
 * termine clasificado como comercio.
 */

const SINONIMOS = {
  inmobiliaria: ['inmobiliaria', 'real estate', 'bienes raices', 'propiedades', 'alquileres', 'inmueble', 'inmuebles'],
  salud: ['salud', 'clinica', 'consultorio', 'medico', 'medicina', 'odontologia', 'dentista', 'estetica', 'psicologo', 'fisioterapia'],
  gastronomia: ['gastronomia', 'restaurante', 'restaurant', 'bar', 'delivery', 'cafeteria', 'parrilla', 'pizzeria', 'catering'],
  retail: ['retail', 'comercio', 'tienda', 'ecommerce', 'e commerce', 'boutique', 'almacen', 'kiosco'],
  servicios_profesionales: ['estudio', 'contable', 'contador', 'abogado', 'juridico', 'consultora', 'consultoria', 'escribania', 'arquitecto'],
  educacion: ['educacion', 'academia', 'instituto', 'cursos', 'capacitacion', 'colegio', 'escuela'],
  automotriz: ['automotriz', 'concesionaria', 'automotora', 'taller', 'autos', 'mecanica', 'repuestos', 'motos'],
};

/** minusculas, sin acentos, sin puntuacion. */
function normalizarTexto(texto) {
  return String(texto || '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Formas equivalentes de una palabra para comparar sin depender del numero.
 * En espaniol el plural es "-s" tras vocal (moto/motos) y "-es" tras consonante
 * (taller/talleres), asi que se generan las dos variantes.
 */
function formas(palabra) {
  const f = new Set([palabra]);
  if (palabra.endsWith('es') && palabra.length > 4) f.add(palabra.slice(0, -2));
  if (palabra.endsWith('s') && palabra.length > 3) f.add(palabra.slice(0, -1));
  return f;
}

function mismaPalabra(a, b) {
  const fa = formas(a);
  for (const fb of formas(b)) {
    if (fa.has(fb)) return true;
  }
  return false;
}

/**
 * Devuelve una clave de SINONIMOS, o 'generico' si no matchea nada.
 *
 * Cuando matchea mas de un rubro (ej. "taller mecanico de motos"), gana el que
 * tenga mas coincidencias; si empatan, el del sinonimo mas largo, que suele ser
 * el mas especifico.
 */
function clasificar(rubroCrudo) {
  const texto = normalizarTexto(rubroCrudo);
  if (!texto) return 'generico';

  const palabras = texto.split(' ');
  const candidatos = [];

  for (const [clave, sinonimos] of Object.entries(SINONIMOS)) {
    if (texto === clave) return clave;

    let coincidencias = 0;
    let masLargo = 0;

    for (const sin of sinonimos) {
      const esFrase = sin.includes(' ');
      const pega = esFrase
        ? texto.includes(sin)
        : palabras.some((p) => mismaPalabra(p, sin));
      if (pega) {
        coincidencias += 1;
        masLargo = Math.max(masLargo, sin.length);
      }
    }

    if (coincidencias > 0) candidatos.push({ clave, coincidencias, masLargo });
  }

  if (!candidatos.length) return 'generico';

  candidatos.sort((a, b) => b.coincidencias - a.coincidencias || b.masLargo - a.masLargo);
  return candidatos[0].clave;
}

module.exports = { clasificar, normalizarTexto, SINONIMOS };
